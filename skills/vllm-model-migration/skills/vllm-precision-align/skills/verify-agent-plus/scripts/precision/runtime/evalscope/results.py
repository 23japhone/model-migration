"""只读核验 EvalScope 原生 predictions、reviews 和 schema v2 报告。"""
from pathlib import Path

from ...evidence import decode, load, sha256
from ...http_evidence import check_capture
from ...results import coverage
from .metrics import number, parse
from .binding import capture_rows, check_snapshot
from .completion import http_prediction, http_record, truncation_summary


def native_prediction(row):
    output = row.get('model_output')
    if not isinstance(output, dict) or output.get('error'):
        raise ValueError('原生预测缺失或含推理错误')
    choices = output.get('choices')
    if not isinstance(choices, list) or len(choices) != 1 or choices[0].get('stop_reason') not in ('stop', 'max_tokens'):
        raise ValueError('原生预测重复生成、失败或结束原因不支持')
    message = choices[0]['message']
    return content_parts(message.get('content'))


def content_parts(content):
    if isinstance(content, str):
        return content, ''
    if not isinstance(content, list):
        raise ValueError('原生预测不是文本')
    text = []; reasoning = []
    for block in content:
        kind = block.get('type')
        if kind == 'text' and isinstance(block.get('text'), str):
            text.append(block['text'])
        elif kind == 'reasoning' and isinstance(block.get('reasoning'), str):
            reasoning.append(block['reasoning'])
        else:
            raise ValueError('原生响应含不支持的内容类型')
    return '\n'.join(text), '\n'.join(reasoning)


def records(path, kind, task, errors):
    rows = []
    try:
        for line in path.read_text(encoding='utf-8').splitlines():
            row = decode(line)
            if not isinstance(row, dict) or type(row.get('index')) != int:
                raise ValueError('原生记录 index 无效')
            row['id'] = row['index']
            if kind == 'predictions':
                row['prediction'] = native_prediction(row)
                if row.get('model') != task.get('served_model', task['model_abbr']):
                    raise ValueError('原生预测模型名不一致')
            else:
                review_record(row)
            rows.append(row)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        errors.append(kind + ' 读取失败：' + str(error))
    return rows


def review_record(row):
    sample = row['sample_score']
    if type(sample.get('sample_id')) != int or sample['sample_id'] != row['index']:
        raise ValueError('review 的样本身份不一致')
    score = sample['score']
    if score.get('status') != 'success' or not score.get('value') or score.get('judge_summary'):
        raise ValueError('评分失败、无结果或使用未支持的 judge')
    values = score['value']
    if not isinstance(values, dict) or any(not (number(value) or type(value) == bool) for value in values.values()):
        raise ValueError('逐样本评分缺少实际有限数值')


def check_report(report, task):
    if report.get('schema_version') != 2 or report.get('dataset_name') != task['benchmark']:
        raise ValueError('原生报告版本或 benchmark 不一致')
    if report.get('model_name') != task['model_abbr']:
        raise ValueError('原生报告模型名不一致')
    summary = report['execution_summary']
    expected = {'requested': len(task['sample_ids']), 'succeeded': len(task['sample_ids']), 'errored': 0}
    for key, value in expected.items():
        if type(summary.get(key)) != int or summary[key] != value:
            raise ValueError('原生执行统计不完整')
    if summary.get('incomplete') is not False or summary.get('subsets') != {task['subset']: expected}:
        raise ValueError('原生 subset 执行不完整')
    if report.get('judge_summary'):
        raise ValueError('首版不支持 LLM judge')


def link_reviews(predictions, reviews, errors):
    by_id = {row['id']: row['prediction'][0] for row in predictions}
    for review in reviews:
        score = review['sample_score']['score']
        if score.get('prediction') != by_id.get(review['id']):
            errors.append('review 与原生预测内容不一致')


def read_task(root, task):
    root = Path(root)
    native = root / 'evalscope' if (root / 'evalscope').is_dir() else root
    abbr = task['model_abbr']; stem = task['benchmark'] + '_' + task['subset'] + '.jsonl'
    paths = [native / kind / abbr / stem for kind in ('predictions', 'reviews')]
    result_path = native / 'reports' / abbr / (task['benchmark'] + '.json')
    errors = []
    predicted = records(paths[0], 'predictions', task, errors)
    reviewed = records(paths[1], 'reviews', task, errors)
    covered = coverage(predicted, task, errors)
    review_coverage = coverage(reviewed, task, errors)
    link_reviews(predicted, reviewed, errors)
    raw = {}
    try:
        raw = load(result_path)
        if not isinstance(raw, dict):
            raw = {}; raise ValueError('报告不是对象')
        check_report(raw, task)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        errors.append('原生报告不可用：' + str(error))
    metrics = parse(raw, task, errors)
    files = {str(p.relative_to(root)): sha256(p) for p in (*paths, result_path) if p.is_file()}
    fingerprint = None
    if task.get('capture_required'):
        fingerprint = check_evidence(root, task, predicted, reviewed, files, errors)
    return {'dataset': task['dataset'], 'model_abbr': abbr, 'valid': not errors, 'metrics': metrics,
            'coverage': covered, 'review_coverage': review_coverage, 'errors': errors, 'files': files,
            'output_truncation': truncation_summary(predicted, reviewed, fingerprint),
            'request_conditions_sha256': fingerprint, 'native_details': raw, 'output_root': str(root)}


def check_evidence(root, task, predicted, reviewed, files, errors):
    try:
        check_snapshot(task, predicted, reviewed, files)
        failures, fingerprint = check_capture(root, files, capture_rows(predicted), task, http_record)
        errors.extend(failures)
        return fingerprint
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        errors.append('样本与请求证据不完整：' + str(error))
        return None
