"""仅使用与本轮质量来源及选中题目绑定的预测包。"""
from alignment.evidence import bound, safe
from alignment.request import fields, reference
from .selection import case_key, degraded

DATA_ERRORS = (OSError, ValueError, TypeError, KeyError)


def package(source, report_sha):
    value = bound(source)
    fields(value, ('format', 'hardware', 'quality_report_sha256', 'evidence_kind', 'cases'))
    if value['format'] != 'token-prediction-bundle-v1' or value['quality_report_sha256'] != report_sha:
        raise ValueError('预测包的格式或首轮质量报告摘要不符')
    if value['hardware'] not in ('gpu', 'npu') or value['evidence_kind'] not in ('synthetic', 'runtime'):
        raise ValueError('预测包缺少设备和真实/模拟来源')
    if not isinstance(value['cases'], list):
        raise ValueError('预测包缺少用例数组')
    return value


def check_record(entry, key, bundle):
    reference(entry['record'])
    value = bound(entry['record'])
    if (value.get('sample_id') != key[1] or value.get('subset', key[0]) != key[0]
            or value.get('evidence_kind', bundle['evidence_kind']) != bundle['evidence_kind']):
        raise ValueError('样本预测内容与预测包的题号、子集或来源不一致')
    for field in ('raw_logits', 'processed_probs', 'output_token_ids', 'prompt_token_ids'):
        if not isinstance(value.get(field), list):
            raise ValueError('预测记录缺少 ' + field)


def index_bundles(sources, report_sha, allowed):
    indexed = {}
    uncovered = []
    for source in sources:
        try:
            bundle = package(source, report_sha)
        except DATA_ERRORS as error:
            uncovered.append({'source': source, 'reason': safe(error)})
            continue
        for case in bundle['cases']:
            try:
                key = case_key(case)
                if key not in allowed:
                    continue
                address = (bundle['hardware'], key)
                if address in indexed:
                    indexed[address] = None
                    raise ValueError('同侧同题有多个预测，不能确定实际请求')
                fields(case, ('subset', 'sample_id', 'record'), ('review', 'cpu'))
                check_record(case, key, bundle)
                indexed[address] = {**case, 'evidence_kind': bundle['evidence_kind']}
            except DATA_ERRORS as error:
                uncovered.append({'source': source, 'reason': safe(error)})
    return indexed, uncovered


def review_scores(entry, device, report_sha):
    source = entry.get('review')
    if not source:
        raise ValueError(device + ' 缺少第二遍同轮逐题评分')
    reference(source)
    value = bound(source)
    if (case_key(value) != case_key(entry) or value.get('hardware') != device
            or value.get('evidence_kind') != entry['evidence_kind']
            or value.get('record_sha256') != entry['record']['sha256']
            or value.get('quality_report_sha256') != report_sha):
        raise ValueError(device + ' 评分与本次预测的身份不符')
    if value.get('status') == 'unavailable':
        raise ValueError(device + ' 同轮逐题评分不可用：' + str(value.get('reason', '缺少评分')))
    if not isinstance(value.get('metrics'), dict):
        raise ValueError('第二遍同轮逐题评分缺少指标')
    return value['metrics']


def reproduce(case, entries, report_sha):
    try:
        gpu, npu = [review_scores(entries[side], side, report_sha) for side in ('gpu', 'npu')]
    except DATA_ERRORS as error:
        return [{'metric': trigger['metric'], 'status': 'not_evaluated', 'reason': safe(error)}
                for trigger in case['triggers']]
    results = []
    for trigger in case['triggers']:
        metric = trigger['metric']
        row = {'metric': metric, 'first_round_subset': trigger['subset']}
        try:
            worse = degraded(gpu[metric], npu[metric], trigger['direction'])
            if trigger['selection_basis'] == 'regressed_sample':
                status = 'reproduced' if worse else 'not_reproduced'
            else:
                status = 'degradation_observed' if worse else 'no_degradation_observed'
            row.update(status=status, gpu_score=gpu[metric], npu_score=npu[metric])
        except DATA_ERRORS as error:
            row.update(status='not_evaluated', reason=safe(error))
        results.append(row)
    return results
