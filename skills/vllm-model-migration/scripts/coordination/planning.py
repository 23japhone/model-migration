"""固定阶段依赖和输入缺口；仅描述动作，不执行动作。"""

STAGES = {
    'preparation': ('vllm-migration-prepare', 'prepare.py'),
    'dataset_preparation': ('vllm-migration-prepare', 'prepare.py'),
    'migration': ('vllm-migration-execute', 'execute.py'),
    'precision': ('vllm-precision-align', 'precision.py'),
}
CONCLUSIONS = ('preparation', 'migration', 'functional_validation', 'precision_testing',
               'evaluation_validity', 'comparability', 'precision_target', 'performance', 'lifecycle')


def execution_reason(request):
    return None


def requirements(request):
    scope = request['scope']
    stages = request.get('stages', {})
    required, missing = set(stages), []
    if scope == 'prepare':
        required.add('preparation')
        if 'preparation' not in stages:
            missing.append('preparation.request')
    if scope in ('deployment', 'deployment_precision'):
        required.add('migration')
        if 'migration' not in stages:
            missing.append('migration.request' if scope == 'deployment' else 'migration')
        if 'result' not in stages.get('migration', {}):
            required.add('preparation')
            if 'preparation' not in stages:
                missing.append('preparation')
    if scope in ('precision', 'deployment_precision'):
        required.add('precision')
        if 'precision' not in stages:
            missing.append('precision.request')
    return required, missing


def precision_dependencies(request, required, missing):
    stages = request.get('stages', {})
    mapped = stages.get('precision', {}).get('bindings', {})
    deps = {source for row in mapped.values() for source in row.values()}
    if request['scope'] == 'deployment_precision' and 'migration' not in deps:
        missing.append('precision.bindings.deployment')
    for source in sorted(deps):
        required.add(source)
        if source not in stages and source not in missing:
            missing.append(source)
    return sorted(deps)


def stage_plan(key, node, request, precision_deps, action):
    skill, entry = STAGES[key]
    mode = 'reuse' if 'result' in node else ('execute' if 'request' in node else 'missing')
    depends = []
    if key == 'migration' and mode != 'reuse':
        depends = ['preparation']
    if key == 'precision':
        depends = precision_deps
    value = {'id': key, 'skill': skill, 'entry': 'scripts/' + entry,
             'action': action if key == 'precision' else 'run', 'mode': mode,
             'status': 'not_run', 'depends_on': depends}
    if key == 'preparation':
        value['purpose'] = 'request_defined' if request['scope'] == 'prepare' else 'deployment'
    if key == 'dataset_preparation':
        value['purpose'] = 'assets'
    return value


def plan(request, action, run_id, parent):
    required, missing = requirements(request)
    deps = precision_dependencies(request, required, missing)
    stages = request.get('stages', {})
    retention = request.get('service_retention', 'keep')
    finalization = 'none' if action == 'analyze' or request['scope'] == 'prepare' else 'keep'
    if retention == 'stop' and finalization != 'none':
        finalization = 'verify-agent:stop_owned_after_consumers'
    return {
        'schema_version': '1.0', 'task_id': 'vllm-model-migration', 'run_id': run_id,
        'operation': action, 'status': 'not_run', 'success': False, 'inputs': request,
        'ready': not missing, 'execution_available': execution_reason(request) is None, 'missing_inputs': sorted(missing),
        'stages': [stage_plan(k, stages.get(k, {}), request, deps, action) for k in STAGES if k in required],
        'service_retention': retention, 'resource_finalization': finalization,
        'conclusions': {key: 'not_evaluated' for key in CONCLUSIONS},
        'evidence': {}, 'evidence_parent': str(parent),
        'uncovered': ['此输出仅为静态计划；运行仍须核验阶段输入与真实依赖',
                      '未读取子请求、旧结果或原始证据，未核对资产、工具、服务和资源归属',
                      '计划路径可用不代表阶段输入有效、具备运行条件或完成真实验收'],
    }
