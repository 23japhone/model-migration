"""在与第二遍相同的前文上用公开 Token 接口复核第三遍目标预测。"""
from pathlib import Path
import shutil

from alignment.evidence import bound, digest, ref, write
from .delegation import compare


class ReplayMismatch(ValueError):
    """重跑前文或同端目标预测已改变。"""

    def __init__(self, reason, **details):
        super().__init__(reason)
        self.details = details


def check_prefix(data, plan, side):
    if (data.get('hardware'), data.get('case'), data.get('model_identity'), data.get('position')) != (
            side, plan['case'], plan['model_identity'], plan['position']):
        return
    observed = data.get('prefix_token_ids')
    expected = plan['prefix_token_ids']
    if not isinstance(observed, list) or observed == expected:
        return
    offset = next((i for i, pair in enumerate(zip(observed, expected)) if pair[0] != pair[1]),
                  min(len(observed),len(expected)))
    raise ReplayMismatch(f'{side} 采集包的前文在零基位置 {offset} 与二遍不同，无法复现目标现场',
                         status='prefix_mismatch', hardware=side)


def local_vector(source, root, directory, name):
    if isinstance(source, list):
        return source
    if not isinstance(source, dict) or set(source) != {'path','sha256','size','dtype'}:
        raise ValueError('第三遍预测向量引用字段无效')
    relative = Path(source['path'])
    path = root / relative
    if relative.is_absolute() or '..' in relative.parts or path.resolve() != path:
        raise ValueError('第三遍预测向量须在本端预测包目录内')
    # Token 任务按样本目录解析向量，独立尝试保存获核验的目标行。
    local = directory / (name + '.bin')
    shutil.copyfile(path, local)
    if digest(local) != source['sha256']:
        raise ValueError('第三遍目标预测向量摘要不符')
    return {**source, 'path': local.name}


def sample(directory, plan, bundle, data):
    kind = data.get('evidence_kind')
    if kind not in ('runtime', 'synthetic'):
        raise ValueError('第三遍采集包缺少真实或模拟来源')
    return target_record(directory, plan, bundle, data['hardware'], kind, data['prediction'])


def target_record(directory, plan, source, label, kind, pred):
    raw = pred['raw_logits']
    size = len(raw) if isinstance(raw, list) else raw['size']
    if size != plan['vocab_size']:
        raise ValueError('第三遍的有效词表大小与二遍不同')
    row = {**plan['case'], 'prompt_token_ids': plan['prefix_token_ids'], 'evidence_kind': kind,
           'output_token_ids': [pred['token_id']], 'stop_reason': 'length'}
    root = Path(source['path']).parent
    for key in ('raw_logits','processed_probs'):
        row[key] = [local_vector(pred[key], root, directory, label + '-' + key)]
    file = directory / (label + '-target.json')
    write(file, row)
    return ref(file)


def check(directory, plan, gpu, npu):
    req = {'case': plan['case'], 'token_alignment': plan['token_alignment'], 'gpu': gpu, 'npu': npu}
    result, source = compare(directory, 'third-pass-prediction', 'token', req)
    actual = sorted(result.get('differences', {}))
    if result['result'] == 'mismatch' and set(plan['triggers']) <= set(actual):
        status = 'reproduced'
    elif result['result'] in ('aligned', 'mismatch'):
        status = 'not_reproduced'
    else:
        status = 'insufficient_evidence'
    return {'status': status, 'position': plan['position'], 'scope': 'target_prediction_on_same_prefix',
            'expected_triggers': plan['triggers'], 'observed_triggers': actual, 'comparison': source}


def check_single(directory, plan, sample_ref, side):
    source = plan['predictions'][side]
    original = bound(source)
    index = plan['position'] - 1
    if original['prompt_token_ids'] + original['output_token_ids'][:index] != plan['prefix_token_ids']:
        raise ValueError('冻结的第二遍预测前文与计划不符')
    prediction = {key: original[key][index] for key in ('raw_logits', 'processed_probs')}
    prediction['token_id'] = original['output_token_ids'][index]
    baseline = target_record(directory, plan, source, 'second-' + side, original.get('evidence_kind', 'unspecified'), prediction)
    request = {'case': plan['case'], 'token_alignment': plan['token_alignment'], 'gpu': baseline, 'npu': sample_ref}
    report, receipt = compare(directory, 'check-' + side + '-prediction', 'token', request)
    if report['result'] == 'mismatch':
        raise ReplayMismatch(f'{side} 的第三遍目标预测与同端第二遍记录不一致：' + report['reason'],
                             status='prediction_mismatch', hardware=side, position=plan['position'], comparison=receipt)
    if report['result'] != 'aligned':
        raise ValueError('第三遍目标位置缺少完整有效的实际预测：' + report['reason'])
    return receipt
