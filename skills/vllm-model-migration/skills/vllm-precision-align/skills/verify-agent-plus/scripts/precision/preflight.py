"""公开无推理预检及正式运行的数据门禁；不探测模型服务。"""
from pathlib import Path
import subprocess

from . import backends
from .contract import check_output
from .evidence import begin, load, now, record, write
from .inputs import dataset, deployment
from .runtime.process import execute


def inputs(request):
    _, _, handoff = deployment(request['deployment'])
    asset = dataset(request['dataset'])
    protected = [handoff['model']['root'], asset['path'], request['evaluator']['config']['path'],
                 request['deployment']['path'], request['dataset']['path']]
    return {'handoff': handoff, 'asset': asset, 'protected': protected}


def check(request, values, attempt):
    root = attempt / 'data-preflight'; root.mkdir()
    payload = root / 'request.json'; output = root / 'result.json'
    write(payload, {'request': request, 'handoff': values['handoff'], 'attempt': str(root)})
    entry = Path(__file__).parent / 'runtime/evalscope/entry.py'
    process = execute([request['evaluator']['python'], '-I', '-B', str(entry), 'preflight', str(payload), str(output)],
                      root / 'process', 60, check=False)
    if process['returncode'] or not output.is_file():
        raise backends.PreparationBlocked('数据预检子进程失败，详见 ' + str(root / 'process/stderr.log'))
    return load(output)


def before_service(request, values, attempt, manifest):
    if backends.resolve(request['evaluator'])[0] != 'evalscope':
        return
    manifest['detailed_report']['phase'] = 'checking_dataset'
    write(manifest['report_path'], manifest)
    result = check(request, values, attempt)
    manifest['detailed_report']['data_preflight'] = result
    write(manifest['report_path'], manifest)
    if result['status'] != 'completed':
        raise backends.PreparationBlocked('; '.join(row['code'] + ': ' + row['message'] for row in result['errors']))
    manifest['detailed_report']['phase'] = 'dataset_ready'
    write(manifest['report_path'], manifest)


def finish(manifest, result):
    status = result['status']; passed = status == 'completed'
    codes = {'completed': 0, 'blocked': 3, 'failed': 1}
    manifest.update(status=status, success=passed, finished_at=now(),
                    summary='数据预检通过；未执行推理或评分' if passed else '数据预检未完成；未执行推理或评分')
    manifest['detailed_report'].update(data_preflight=result, phase='dataset_ready' if passed else 'dataset_' + status)
    manifest['uncovered'].append('仅核对本地数据与配置；未验证服务、精度、目标或官方 latest 版本')
    write(manifest['report_path'], manifest)
    return codes[status], manifest


def run(request, parent, run_id):
    try:
        if backends.resolve(request['evaluator'])[0] != 'evalscope':
            raise ValueError('preflight 本轮仅支持 EvalScope；AISBench 的原有 run/analyze 保持不变')
        values = inputs(request)
        check_output(parent, values['protected'])
    except (OSError, ValueError, KeyError, TypeError) as error:
        report = record(request, run_id, 'preflight', 'blocked')
        report['summary'] = str(error)
        return 3, report
    attempt, manifest = begin(parent, request, run_id, 'preflight')
    manifest['detailed_report']['phase'] = 'checking_dataset'; write(manifest['report_path'], manifest)
    try:
        result = check(request, values, attempt)
    except (KeyboardInterrupt, SystemExit) as error:
        result = {'status': 'failed', 'tasks': [], 'errors': [
            {'code': 'dataset_preflight_cancelled', 'type': type(error).__name__, 'message': str(error)}]}
    except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
        result = {'status': 'blocked', 'tasks': [], 'errors': [
            {'code': 'dataset_preflight_failed', 'type': type(error).__name__, 'message': str(error)}]}
    return finish(manifest, result)
