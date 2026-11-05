"""核验公开阶段回执和原始证据，不复刻任务实现或重新评分。"""
from pathlib import Path

from .sources import bound, parse, read, ref, stable_bytes

VERDICTS = {'pass', 'fail', 'not_evaluated', 'review_required'}
VERIFY_KEYS = {'inputs','runtime','registration','workers','functional_protocol','semantic','lifecycle'}


def stage_plan(value, key, request, run_id, action):
    if key == 'preparation':
        expected = preparation_inputs(request)
        if value.get('purpose') != request['purpose'] or set(value['tasks']) != set(expected['tasks']):
            raise ValueError('准备计划的范围与委托不一致')
    if key == 'migration' and value.get('action') != 'run':
        raise ValueError('迁移计划操作与委托不一致')
    if key == 'precision':
        expected = {'task_id':'vllm-precision-align','run_id':run_id,'operation':action,'inputs':request}
        if any(value.get(k) != v for k, v in expected.items()):
            raise ValueError('精度计划身份或输入与委托不一致')


def common(value, skill):
    if not isinstance(value, dict):
        raise ValueError('回执须为对象')
    if value.get('schema_version') != '1.0' or value.get('task_id') != skill:
        raise ValueError('回执 schema/task 身份不符')
    for key, kind in [('inputs',dict), ('conclusions',dict), ('evidence',dict), ('uncovered',list)]:
        if not isinstance(value.get(key), kind):
            raise ValueError('回执共同字段缺失：'+key)
    if not value['conclusions'] or any(v not in VERDICTS for v in value['conclusions'].values()):
        raise ValueError('回执结论无效')
    if value.get('status') not in ('completed', 'failed', 'blocked'):
        raise ValueError('回执不是终态')


def original_evidence(value):
    detail = value.get('detailed_report', {})
    if not isinstance(detail, dict) or not isinstance(detail.get('files', {}), dict):
        raise ValueError('原始详细报告或文件索引类型无效')
    if value.get('task_id') == 'verify-agent':
        from .continuation import verify_responses
        verify_responses(value)
    files = detail.get('files', {})
    if value['status'] == 'completed' and not value['evidence'] and not files:
        raise ValueError('完成回执缺少原始证据')
    for path in value['evidence'].values():
        if not isinstance(path, str):
            raise ValueError('证据路径类型无效')
        stable_bytes(path)
    for path, digest in files.items():
        if ref(Path(path))['sha256'] != digest:
            raise ValueError('原始证据摘要不一致')


def child(row, skill, run_id=None):
    if not isinstance(row, dict):
        raise ValueError('任务台账条目须为对象')
    source = row.get('receipt')
    if source is None and row.get('manifest'):
        source = {'path': row['manifest'], 'sha256': row['manifest_sha256']}
    if source is None:
        if row.get('status') == 'completed':
            raise ValueError('完成任务缺少原始回执')
        return None
    value = bound(source)
    common(value, skill)
    if run_id is not None and value.get('run_id') != run_id:
        raise ValueError('原始任务 run 身份不符')
    original_evidence(value)
    if row.get('status') == 'completed' and value['status'] != 'completed':
        raise ValueError('台账完成状态与原始任务不符')
    if row.get('conclusions') != value['conclusions']:
        raise ValueError('台账结论与原始任务不符')
    if 'request' in row and row['request'] != value['inputs']:
        raise ValueError('台账输入与原始任务不符')
    return value


def prepare(value):
    tasks = value['tasks']
    if not tasks or set(tasks) - {'model','image','dataset'}:
        raise ValueError('准备任务台账缺失或越界')
    expected = value['inputs']['tasks']
    if not isinstance(expected, dict) or set(tasks) != set(expected):
        raise ValueError('准备台账与请求任务范围不一致')
    required = []
    for name, row in tasks.items():
        raw = child(row, 'vllm-'+name+'-prepare', value['run_id'])
        if raw is not None and row.get('asset') != raw.get('asset'):
            raise ValueError('准备台账资产与原始任务不一致')
        if type(row.get('required')) is not bool or row['required'] != expected[name]['required']:
            raise ValueError('准备任务 required 与请求不一致')
        if row.get('required'):
            required.append(row)
    if value['status'] == 'completed':
        preparation_complete(value, required)


def preparation_complete(value, required):
    tasks = value['tasks']
    if not required or any(r['status'] != 'completed' or r['conclusions'].get('assets') != 'pass' for r in required):
        raise ValueError('准备伪 completed')
    if value['conclusions'].get('preparation') != 'pass' or value['conclusions'].get('assets') != 'pass':
        raise ValueError('准备必需结论未通过')
    if value['conclusions'].get('version_materials') in ('fail', 'review_required'):
        raise ValueError('准备版本资料门禁未通过')
    if value['inputs'].get('purpose') == 'deployment':
        if any(tasks.get(k, {}).get('status') != 'completed' for k in ('model','image')):
            raise ValueError('部署准备缺少模型或镜像')
        if value['conclusions'].get('version_materials') != 'pass':
            raise ValueError('部署版本资料未通过')


def migration(value):
    if 'source' in value['inputs']:
        from .continuation import finalized
        raw, precheck = finalized(value)
        migration_route(value, precheck)
        migration_complete(value, raw)
        return
    owners = {'precheck': 'vllm-model-precheck', 'adapt': 'adapt-agent',
              'register': 'vllm-model-register', 'verify': 'verify-agent'}
    raw, precheck = None, None
    for key, row in value['tasks'].items():
        result = child(row, owners[key], None if key == 'adapt' else value['run_id'])
        if key == 'verify':
            raw = result
        if key == 'precheck':
            precheck = result
    if value['status'] == 'completed':
        migration_route(value, precheck)
        migration_complete(value, raw)


def migration_complete(value, raw):
    if any(value['tasks'].get(key, {}).get('status') != 'completed' for key in ('precheck', 'verify')):
        raise ValueError('迁移缺少完成的预检或功能验证台账')
    if raw is None or raw.get('success') is not True or any(raw['conclusions'].get(k) != 'pass' for k in VERIFY_KEYS):
        raise ValueError('迁移缺少全部通过的原始功能回执')
    if any(value['conclusions'].get(k) != 'pass' for k in ('preparation','migration','functional_validation')):
        raise ValueError('迁移必需结论不完整')
    bound(value['deployment'])
    from .continuation import deployment_reference
    source = deployment_reference(raw)
    if value['deployment'] != source:
        raise ValueError('迁移部署交接未绑定原始 verify 回执')


def expected_code(value, key):
    if value['status'] != 'completed':
        return 3 if value['status'] == 'blocked' else 1
    if key == 'precision':
        rows = value['tasks']['evaluations'].values()
        passed = all(not r.get('targets_requested') or r['conclusions'].get('precision_target') == 'pass' for r in rows)
        if value.get('success') is not passed:
            raise ValueError('精度 success 与目标不一致')
        return 0 if passed else 1
    return 0


def verify_files(value, path, key):
    evidence = value['evidence']
    if evidence.get('manifest') != str(path):
        raise ValueError('manifest 位置不一致')
    for name in ('ledger','handoff'):
        expected = path.parent/('task-ledger.json' if name == 'ledger' else {
            'preparation': 'prepare-handoff.json', 'migration': 'migration-handoff.json',
            'precision': 'precision-stage-handoff.json'}[key])
        if evidence.get(name) != str(expected):
            raise ValueError('阶段证据越出本次尝试')
    ledger = read(evidence['ledger'])
    if ledger != value.get('tasks'):
        raise ValueError('阶段台账与 manifest 不一致')
    handoff = read(evidence['handoff'])
    handoff_content(value, handoff, key)
    if handoff.get('conclusions') != value['conclusions']:
        raise ValueError('handoff 结论不一致')
    if key == 'preparation':
        if handoff.get('phase_manifest') != str(path) or handoff.get('run_id') != value['run_id']:
            raise ValueError('准备 handoff 身份不符')
    else:
        binding = handoff['stage' if key == 'migration' else 'ledger']
        if binding != ref(Path(evidence['ledger'])) or bound(binding) != ledger:
            raise ValueError('handoff 未绑定最终台账')
    if key == 'precision' and bound(value['handoff']) != handoff:
        raise ValueError('精度 handoff 摘要不一致')


def validate(value, path, key, skill):
    common(value, skill)
    if not isinstance(value.get('tasks'), dict):
        raise ValueError('阶段 tasks 须为对象')
    if any(not isinstance(row, dict) for row in value['tasks'].values()):
        raise ValueError('阶段台账条目须为对象')
    verify_files(value, path, key)
    if key == 'precision':
        from .precision_receipts import precision
        precision(value, child)
    else:
        {'preparation': prepare, 'migration': migration}[key](value)
    return value


def receipt(text, code, key, skill, request, root, run_id, action, plan=None):
    printed = parse(text)
    if not {'manifest', 'status', 'conclusions'} <= printed.keys():
        raise ValueError('阶段 stdout 缺少必需共同字段')
    path = Path(printed['manifest'])
    if not path.is_absolute() or path.resolve() != path or not path.is_relative_to(root) or len(path.relative_to(root).parts) != 2:
        raise ValueError('回执不属于本次阶段独立运行')
    value = read(path)
    validate(value, path, key, skill)
    matches = preparation_matches(value, request, plan) if key == 'preparation' else value['inputs'] == request
    if value['run_id'] != run_id or not matches:
        raise ValueError('阶段 run/input 身份不符')
    if key == 'precision' and value.get('operation') != action:
        raise ValueError('精度操作身份不一致')
    for field in ('status','conclusions','inputs','run_id','task_id','success','operation','next_action'):
        if field in printed and printed[field] != value.get(field):
            raise ValueError('stdout 与持久回执不一致')
    if code != expected_code(value, key):
        raise ValueError('退出码与回执状态不一致')
    return value, ref(path)


def preparation_inputs(request):
    expected = {**request, 'tasks': dict(request['tasks'])}
    forced = {'assets': (), 'precheck': ('model',), 'deployment': ('model','image'),
              'deployment_precision': ('model','image','dataset')}[request['purpose']]
    for key in forced:
        expected['tasks'].setdefault(key, {'required': True, 'error': '本次范围缺少必需任务请求'})
    return expected


def preparation_matches(value, request, plan):
    expected = preparation_inputs(request)
    actual = value['inputs']
    if {k:v for k,v in expected.items() if k != 'tasks'} != {k:v for k,v in actual.items() if k != 'tasks'}:
        return False
    if set(actual['tasks']) != set(expected['tasks']):
        return False
    for name, spec in expected['tasks'].items():
        item = actual['tasks'][name]
        row = value['tasks'][name]
        planned = (plan or {}).get('tasks', {}).get(name, {})
        if not preparation_task_matches(item, spec, row, planned):
            return False
    return True


def preparation_error(item, spec, row, planned):
    if not isinstance(item, dict) or set(item) != {'required', 'error'} or not isinstance(item['error'], str) or not item['error']:
        return False
    required = bool(spec.get('required', True)) if isinstance(spec, dict) else True
    if isinstance(spec, dict) and set(spec) - {'request','required','python','timeout_seconds'}:
        required = True
    return (item['required'] is required and row['status'] == 'blocked' and planned.get('status') == 'blocked'
            and not row.get('manifest') and not row.get('receipt'))


def handoff_content(value, handoff, key):
    if handoff.get('schema_version') != '1.0':
        raise ValueError('handoff schema 无效')
    fields = {'preparation': ('run_id','uncovered'),
              'migration': ('tasks','route','deployment','uncovered'),
              'precision': ('task_id','run_id','status','tasks','results','resources','uncovered')}[key]
    if any(handoff.get(k) != value.get(k) for k in fields):
        raise ValueError('handoff 内容或身份与阶段 manifest 不一致')
    if key == 'preparation':
        expected = {name: {field: row.get(field) for field in (
            'required','status','manifest','manifest_sha256','asset','conclusions')} for name,row in value['tasks'].items()}
        if handoff.get('assets') != expected or handoff.get('target') != value['inputs'].get('target'):
            raise ValueError('准备 handoff 资产与台账不一致')
        if handoff.get('version_review', []) != value.get('version_review', []):
            raise ValueError('准备 handoff 版本复核不一致')


def preparation_task_matches(item, spec, row, planned):
    # 公开阶段负责规范化；总入口绑定 plan/run 的输入，拒绝运行时更换任务。
    if row.get('manifest') and ('request' not in planned or row.get('request') != planned['request']):
        return False
    if item == spec:
        return True
    # 无效单项只允许原阶段以未执行的 error 回执保留，不扩大失败范围。
    return preparation_error(item, spec, row, planned)


def migration_route(value, precheck):
    route = value.get('route')
    if route not in ('supported_candidate', 'registration_required', 'adaptation_required') or precheck is None:
        raise ValueError('迁移 completed 缺少本切片可完成的预检路线')
    if not isinstance(precheck.get('details'), dict) or precheck['details'].get('route') != route:
        raise ValueError('阶段路线与原始预检不一致')
    if route in ('registration_required', 'adaptation_required'):
        row = value['tasks'].get('register', {})
        if row.get('status') != 'completed' or row.get('conclusions', {}).get('registration') != 'pass':
            raise ValueError('预检要求注册，但缺少通过的注册回执')

    if route == 'adaptation_required':
        from .continuation import adaptation
        # finalize 的路线证据保留在已验证的原运行阶段。
        original = bound(value['inputs']['source']) if 'source' in value['inputs'] else value
        adaptation(original, precheck)
