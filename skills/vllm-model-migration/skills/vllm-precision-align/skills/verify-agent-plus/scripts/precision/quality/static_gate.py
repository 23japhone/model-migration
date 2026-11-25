"""为自动发现的 GPU 基线，在调用模型前核对可跨设备验证的静态条件。"""
import re

from ..inputs import canonical


class Mismatch(ValueError):
    """自动基线不满足本次测量的必要条件，可跳过该测量。"""


def digest(value):
    return isinstance(value, str) and bool(re.fullmatch('[0-9a-f]{64}', value))


def valid_generation(value):
    if not isinstance(value, dict) or not isinstance(value.get('overrides'), dict):
        return False
    files = value.get('model_generation_files')
    return (bool(value['overrides'].get('--generation-config')) and isinstance(files, dict)
            and all(digest(identity) for identity in files.values()))


def verified(field, current, baseline, validator):
    actual = current.get(field)
    if not validator(actual) or canonical(actual) != canonical(baseline.get(field)):
        raise Mismatch('自动 GPU 基线与 NPU 缺少一致的静态证据：' + field)


def unseeded_few_shot(prompt):
    return (prompt.get('few_shot_random') is True and prompt.get('few_shot_num') != 0
            and type(prompt.get('few_shot_seed')) is not int)


def check_readers(request, current, baseline):
    random_examples = unseeded_few_shot(request['protocol']['prompt_config'])
    actual = current.get('reader_sha256_by_subset') or {}
    previous = baseline.get('reader_sha256_by_subset') or {}
    if not isinstance(actual, dict) or not isinstance(previous, dict):
        raise Mismatch('reader_sha256_by_subset 须按子集固定原生输入')
    scopes = {s['subset'] for s in request['scores']}
    subsets = request['protocol']['subsets'] if not scopes or None in scopes else sorted(scopes)
    for subset in subsets:
        if random_examples or subset in actual and subset in previous:
            if not digest(actual.get(subset)) or actual[subset] != previous.get(subset):
                raise Mismatch('自动 GPU 基线与 NPU 的 reader_sha256_by_subset 未固定或不同：' + subset)


def check(request, current):
    if 'expected_static_conditions' not in request:
        return
    baseline = request['expected_static_conditions']
    if not isinstance(baseline, dict) or not isinstance(current, dict):
        raise Mismatch('自动 GPU 基线或 NPU 缺少静态条件')
    for key in ('chat_template_sha256', 'tokenizer_sha256'):
        verified(key, current, baseline, digest)
    verified('server_generation', current, baseline, valid_generation)
    budget = request['protocol']['generation_config'].get('max_tokens')
    if type(budget) is not int or budget <= 0:
        verified('effective_max_tokens', current, baseline, lambda v: type(v) is int and v > 0)
    check_readers(request, current, baseline)
