"""冻结样本、原生 prompt、参考答案与原始 HTTP 的对应关系。"""
import json

from ...evidence import load, verify_files


def messages_identity(messages):
    if not isinstance(messages, list) or not messages:
        raise ValueError('缺少原生消息')
    value = [{'role': row['role'], 'content': row['content']} for row in messages]
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def target_values(value):
    values = value if isinstance(value, list) else [value]
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))


def check_snapshot(task, predicted, reviewed, files):
    reference = task.get('sample_snapshot')
    if reference is None:
        raise ValueError('缺少冻结原生样本身份')
    verify_files({reference['path']: reference['sha256']})
    samples = load(reference['path'])
    files[reference['path']] = reference['sha256']
    if len(samples) != len(task['sample_ids']):
        raise ValueError('冻结样本数量不一致')
    expected = dict(zip(task['sample_ids'], samples))
    for row in predicted:
        if messages_identity(row['messages'][:-1]) != messages_identity(expected[row['id']]['input']):
            raise ValueError('原生 prompt 与冻结样本不一致')
    for row in reviewed:
        if row.get('target') != target_values(expected[row['id']]['target']):
            raise ValueError('原生评分参考与冻结样本不一致')


def capture_rows(predicted):
    reasons = {'stop': 'stop', 'max_tokens': 'length'}
    return [{'prediction': (messages_identity(row['messages'][:-1]), row['prediction'],
                            reasons[row['model_output']['choices'][0]['stop_reason']])} for row in predicted]
