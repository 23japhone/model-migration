"""总入口形状与输出边界检查；不读取任何子请求或交接。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[2] / "scripts"))
from skill_paths import plugin_root as release_root, skill_root as release_skill, owned_files, registration_bundle
from pathlib import Path
import re

SCOPES = {
    'prepare': {'preparation'},
    'deployment': {'preparation', 'migration'},
    'deployment_precision': {'preparation', 'dataset_preparation', 'migration', 'precision'},
    'precision': {'dataset_preparation', 'precision'},
}


def fields(value, required, optional=()):
    if not isinstance(value, dict):
        raise ValueError('需要 JSON 对象')
    if set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError('对象字段缺失或含未知字段')


def name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,80}', value):
        raise ValueError('标识须为安全单层名称')


def reference(value):
    fields(value, ('path', 'sha256'))
    path = value['path']
    if not isinstance(path, str) or '\0' in path:
        raise ValueError('引用须为规范绝对文件路径')
    parsed = Path(path)
    if not parsed.is_absolute() or str(parsed) != path or '..' in parsed.parts or path == '/' or path.startswith('//'):
        raise ValueError('引用须为规范绝对文件路径')
    if not isinstance(value['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', value['sha256']):
        raise ValueError('引用 SHA256 无效')


def bindings(value, scope):
    if not isinstance(value, dict):
        raise ValueError('精度绑定须为评测 id 到依赖的对象')
    for key, row in value.items():
        name(key)
        fields(row, (), ('deployment', 'dataset'))
        if not row:
            raise ValueError('单项精度绑定不能为空')
        if 'dataset' in row and row['dataset'] != 'dataset_preparation':
            raise ValueError('数据绑定只能指向 dataset_preparation')
        if 'deployment' in row:
            if scope != 'deployment_precision' or row['deployment'] != 'migration':
                raise ValueError('部署绑定仅用于完整流程的 migration 交接')


def stage(value, key, scope):
    fields(value, (), ('request', 'result', 'bindings'))
    sources = set(value) & {'request', 'result'}
    if len(sources) != 1:
        raise ValueError('阶段必须且只能提供 request 或 result 引用')
    reference(value[next(iter(sources))])
    if 'result' in value:
        fresh = key == 'precision' or (scope, key) in {('prepare', 'preparation'), ('deployment', 'migration')}
        if fresh:
            raise ValueError('该请求范围需要阶段 request，不能用历史 result 代替')
    if 'bindings' in value:
        if key != 'precision' or 'request' not in value:
            raise ValueError('只有精度阶段请求可声明 bindings')
        bindings(value['bindings'], scope)


def stage_relationships(stages):
    mapped = stages.get('precision', {}).get('bindings', {})
    uses_data = any('dataset' in row for row in mapped.values())
    if 'dataset_preparation' in stages and not uses_data:
        raise ValueError('数据准备没有精度消费者')
    if 'preparation' in stages and 'result' in stages.get('migration', {}):
        raise ValueError('复用迁移结果时不能额外执行无消费者的模型镜像准备')


def analysis_scope(value, retention):
    stages = value.get('stages', {})
    if value['scope'] != 'precision' or set(stages) - {'precision'} or retention != 'keep':
        raise ValueError('历史分析只允许精度阶段，不准备、部署或停止服务')
    if 'bindings' in stages.get('precision', {}):
        raise ValueError('历史分析不得绑定准备或部署依赖')


def options(value, action):
    timeout = value.get('timeout_seconds', 14400)
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError('超时须为 1—86400 秒整数')
    retention = value.get('service_retention', 'keep')
    if retention not in ('keep', 'stop'):
        raise ValueError('服务最终策略只能为 keep 或 stop')
    if value['scope'] == 'prepare' and retention == 'stop':
        raise ValueError('仅准备范围没有服务停止职责')
    if 'performance_target' in value:
        target = value['performance_target']
        if not isinstance(target, str) or not target.strip():
            raise ValueError('性能目标须为非空文本，只保留不评估')
    if action == 'analyze':
        analysis_scope(value, retention)


def validate(value, action):
    fields(value, ('schema_version', 'scope'), ('stages', 'service_retention', 'performance_target', 'timeout_seconds'))
    if value['schema_version'] != '1.0':
        raise ValueError('仅支持 schema_version=1.0')
    scope = value['scope']
    if not isinstance(scope, str) or scope not in SCOPES:
        raise ValueError('scope 不在总入口支持范围')
    stages = value.get('stages', {})
    fields(stages, (), SCOPES[scope])
    for key, item in stages.items():
        stage(item, key, scope)
    stage_relationships(stages)
    options(value, action)
    return value


def output(work_root, run_id):
    name(run_id)
    target = Path(work_root).expanduser().absolute() / run_id / 'vllm-model-migration'
    if target.resolve() != target:
        raise ValueError('输出不能经符号链接重定向或包含非规范路径')
    installed = release_root()
    if target.is_relative_to(installed) or installed.is_relative_to(target):
        raise ValueError('运行目录须位于完整安装资源之外')
    return target
