"""从绑定请求和成功回执恢复性能环境；不根据目录或当前 shell 猜选择。"""
from pathlib import Path

from coordination.request import fields, reference
from coordination.runtime import sources
from ..contract import normalize
from . import history


def request(value):
    fields(value, ('customer_manifest',))
    reference(value['customer_manifest'])
    return value


def environment(value, row):
    installation = sources.bound(row['request'])
    result = sources.bound(row['receipt']).get('result')
    if not isinstance(result, dict) or not isinstance(result.get('versions'), dict):
        raise ValueError('安装结果或版本记录类型无效')
    recovered = history.recovered(value, row)
    options = recovered['options']
    if 'evalscope_install_selection' in value:
        selected = history.recorded(value)['options']
        if any(selected[key] != options[key] for key in ('python', 'venv')):
            raise ValueError('初始安装选择与成功请求不一致，要求新 run-id')
    if any(installation['install'].get(flag) for flag in ('help', 'dry_run')):
        raise ValueError('帮助或预览请求不能交接环境')
    return result_identity(result, options)


def result_identity(result, options):
    cli = result.get('evalscope_executable')
    scripts = Path(history.runtime_python(options['venv'])).parent
    if cli not in [str(scripts / name) for name in ('evalscope', 'evalscope.exe')]:
        raise ValueError('安装 CLI 与已绑定 venv 不一致')
    expected = {'benchmark_python': result['python'], 'venv': result['venv'],
                'evalscope_executable': cli, 'evalscope_version': result['evalscope_version']}
    original = result.get('performance_handoff')
    fields(original, ('benchmark_python', 'evalscope_executable', 'evalscope_version', 'env'), ('venv',))
    if any(original[key] != expected[key] for key in expected if key in original):
        raise ValueError('性能交接字段与成功安装结果不一致')
    fields(original['env'], ('PATH',))
    if not isinstance(original['env']['PATH'], str) or '\0' in original['env']['PATH']:
        raise ValueError('历史交接 PATH 无效')
    return expected


def customer_identity(manifest, run_id):
    if (manifest.get('schema_version') != '1.0' or manifest.get('task_id') != 'vllm-model-migration-customer'
            or manifest.get('run_id') != run_id):
        raise ValueError('客户回执与本次 run-id 不一致')
    if normalize(sources.bound(manifest['task_source'])) != manifest.get('inputs'):
        raise ValueError('客户输入快照与原始 task 不一致')
    effective = manifest.get('effective_inputs', manifest.get('declared_inputs', manifest['inputs']))
    if effective['accuracy']['evaluator'] != 'evalscope':
        raise ValueError('最终评测后端不是 EvalScope，不能交接此环境')


def load(value, run_id):
    manifest = sources.bound(value['customer_manifest'])
    customer_identity(manifest, run_id)
    steps = manifest.get('steps')
    if not isinstance(steps, dict):
        raise ValueError('客户步骤台账类型无效')
    row = steps.get('environment')
    if not isinstance(row, dict) or row.get('status') != 'completed':
        raise ValueError('环境步骤尚未完成，不能交接')
    binding = manifest.get('evalscope_environment')
    if not isinstance(binding, dict) or binding.get('invalidated'):
        raise ValueError('环境绑定缺失或失效，要求新 run-id')
    result = environment(manifest, row)
    refs = {**value, 'task_source': manifest['task_source'],
            'installation_request': row['request'], 'installation_receipt': row['receipt']}
    if 'evalscope_install_selection' in manifest:
        refs['installation_selection'] = manifest['evalscope_install_selection']['source']
    for ref in refs.values():
        sources.bound(ref)
    protected = sources.paths(manifest, Path(value['customer_manifest']['path']).parent)
    protected.extend(Path(ref['path']) for ref in refs.values())
    protected.append(Path(result['venv']))
    return result, refs, protected
