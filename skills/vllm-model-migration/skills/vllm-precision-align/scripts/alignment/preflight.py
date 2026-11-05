"""所有就绪 EvalScope 项先完成数据预检，错误只阻塞其消费者。"""
from .delegation import delegate, TaskBlocked
from .evidence import EvidenceError, save, safe
from .sources import INPUT_ERRORS


def required(item, request):
    if 'result' in item:
        return False
    evaluator = request.get('evaluator', {})
    backend = evaluator.get('backend')
    if backend is None:
        backend = 'aisbench' if evaluator.get('config', {}).get('path', '').endswith('.py') else 'evalscope'
    return backend == 'evalscope'


def evaluations(request, values, attempt, manifest, timeout, prepare):
    for item in request['evaluations']:
        row = manifest['tasks']['evaluations'][item['id']]
        if row['status'] == 'blocked':
            continue
        try:
            child = prepare(item, values, manifest)
            if not required(item, child):
                continue
            row['phase'] = 'checking_dataset'; save(attempt, manifest)
            value, reference = delegate('evaluations', item['id'], child, attempt, manifest['run_id'], timeout, 'preflight')
            row['preflight'] = reference
            row['phase'] = 'dataset_ready' if value['status'] == 'completed' else 'dataset_blocked'
            if value['status'] != 'completed':
                row.update(status='blocked', diagnostic=value.get('detailed_report', {}).get('data_preflight', {}),
                           reason=value.get('summary', '数据预检未通过'))
        except EvidenceError:
            raise
        except (*INPUT_ERRORS, RuntimeError, TaskBlocked) as error:
            row.update(status='blocked', diagnostic={'type': type(error).__name__, 'message': safe(error)})
        finally:
            save(attempt, manifest)


def receipt(value, code):
    details = value.get('detailed_report', {})
    if any(verdict != 'not_evaluated' for verdict in value['conclusions'].values()):
        raise ValueError('数据预检不得声称已取得精度或目标结论')
    passed = value['status'] == 'completed'
    if passed and (details.get('data_preflight', {}).get('status') != 'completed' or value.get('success') is not True):
        raise ValueError('数据预检缺少完成证据')
    if code != {'completed': 0, 'blocked': 3, 'failed': 1}.get(value['status']):
        raise ValueError('数据预检退出码与回执不一致')
