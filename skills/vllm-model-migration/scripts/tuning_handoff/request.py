"""导出请求、只读计划及输出安装边界。"""
from pathlib import Path

from coordination.request import fields, name, output, reference

FILES = {
    'benchmark_plan': 'sglang-performance-benchmark/config/benchmark_plan.json',
    'accuracy_plan': 'sglang-accuracy-benchmark/config/accuracy_plan.json',
    'deployment': 'sglang-model-deployment/config/deployment.json',
}


def validate(value):
    fields(value, ('schema_version', 'entry_manifest', *FILES), ('runtime_environment',))
    if value['schema_version'] != '1.0':
        raise ValueError('仅支持交接请求 schema_version=1.0')
    for key in ('entry_manifest', *FILES, 'runtime_environment'):
        if key in value:
            reference(value[key])
    return value


def parent(work_root, run_id):
    name(run_id)
    # 复用总入口的完整安装根保护，包括嵌套发布副本。
    output(work_root, run_id)
    return Path(work_root).expanduser().absolute()/run_id/'vllm-model-migration-handoff'


def plan(value, destination):
    return {'schema_version': '1.0', 'status': 'not_run', 'ready': True,
            'inputs': value, 'evidence_parent': str(destination),
            'deliverables': list(FILES.values()),
            'uncovered': ['只核对顶层请求形状；未读取引用、核对资产或证明基线有效性']}
