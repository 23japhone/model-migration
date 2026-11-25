"""从首轮已固定的部署交接提取 CPU 参照模型身份。"""
import hashlib
import json

from alignment.evidence import bound, safe
from alignment.request import reference
from .bundles import DATA_ERRORS


def plain_server(overrides):
    if not isinstance(overrides, dict) or overrides.get('--generation-config') != 'vllm':
        return False
    allowed = {'--generation-config', '--dtype', '--seed'}
    dtypes = ('auto', 'half', 'float16', 'bfloat16', 'float', 'float32')
    return (not (overrides.keys() - allowed) and overrides.get('--dtype', 'auto') in dtypes
            and str(overrides.get('--seed', '0')).removeprefix('-').isdecimal())


def plain_greedy(projection):
    """只认原生 vLLM 默认参数与可穷举的中性请求参数。"""
    generation = projection['protocol'].get('generation_config', {})
    static = projection.get('static_conditions') or projection.get('runtime_conditions') or {}
    overrides = (static.get('server_generation') or {}).get('overrides')
    neutral = {'top_p': 1, 'top_k': -1, 'min_p': 0, 'repetition_penalty': 1,
               'frequency_penalty': 0, 'presence_penalty': 0, 'min_tokens': 0}
    extras = generation.get('extra_body') or {}
    template_only = isinstance(extras, dict) and not (extras.keys() - {'chat_template_kwargs'})
    neutral.update(stream=False, retries=1, n=1)
    allowed = {'temperature', 'max_tokens', 'stop', 'stop_seqs', 'stop_token_ids', 'seed', 'extra_body', *neutral}
    return (plain_server(overrides) and generation.get('temperature') == 0
            and template_only and not isinstance(generation.get('temperature'), bool) and not (generation.keys() - allowed)
            and all(generation.get(key, default) == default for key, default in neutral.items()))


def verified_deployment(source):
    checked = bound(source)
    if (checked.get('task_id'), checked.get('status'), checked.get('success')) != ('verify-agent', 'completed', True):
        raise ValueError('CPU 模型绑定缺少已验收部署')
    if checked.get('operation') != 'review':
        return checked
    raw = bound(checked['inputs']['source'])
    if (raw.get('task_id'), raw.get('status'), raw.get('conclusions', {}).get('semantic')) != (
            'verify-agent', 'completed', 'review_required'):
        raise ValueError('语义复核缺少相应的原始待复核报告')
    if checked['evidence']['handoff'] != raw['evidence']['handoff'] or checked['conclusions'].get('semantic') != 'pass':
        raise ValueError('部署复核与原始交接或语义结论不一致')
    return raw


def model_binding(request, report, projection):
    if not projection:
        return None
    native = request.get('native_request')
    if not native:
        receipt = report['evidence']['receipt']
        reference(receipt)
        native = bound(receipt)['inputs']
    source = native['measurement']['deployment']
    reference(source)
    verification = verified_deployment(source)
    handoff = bound({'path': verification['evidence']['handoff'],
                     'sha256': verification['detailed_report']['handoff_sha256']})
    model = handoff['model']
    identity = projection['protocol']['model_identity']
    canonical = json.dumps(model['files'], sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    if (model != verification['inputs']['model'] or hashlib.sha256(canonical).hexdigest() != identity
            or native['protocol']['model_identity'] != identity):
        raise ValueError('CPU 模型文件清单与首轮测量的模型身份不符')
    return {**{key: model[key] for key in ('root', 'files', 'weight_files')},
            'model_identity': identity, 'plain_greedy': plain_greedy(projection)}


def optional_model(request, report, native):
    try:
        return model_binding(request, report, native.npu)
    except DATA_ERRORS as error:
        native.issues.append('不能由现有部署交接确定 CPU 参照模型：' + safe(error))
        return None
