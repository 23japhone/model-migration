"""公开质量测量请求与派生协议的结构。"""
from .. import contract
from ..backends import resolve

PROTOCOL_FIELDS = ('evaluator', 'evaluator_version', 'dataset', 'dataset_revision', 'task_name',
                   'task_revision', 'subsets', 'split', 'model_identity', 'sample_identities',
                   'dataset_identity', 'prompt_config', 'generation_config', 'scoring_config', 'metrics')
SCORE_FIELDS = ('selector', 'unit', 'direction', 'sample_metric', 'sample_kind')


def verify_specs(request):
    protocol = request['protocol']
    scopes = set()
    for row in request['scores']:
        contract.object_fields(row, ('metric', 'subset', *SCORE_FIELDS))
        name, subset = row['metric'], row['subset']
        contract.name(name)
        if subset is not None and subset not in protocol['subsets']:
            raise ValueError('指标的 subset 未被测量')
        if (name, subset) in scopes:
            raise ValueError('指标比较范围重复')
        scopes.add((name, subset))
        expected = {key: row[key] for key in SCORE_FIELDS}
        if expected != protocol['metrics'].get(name):
            raise ValueError('指标请求与冻结协议定义不同')
        contract.metric({key: row[key] for key in ('selector', 'unit', 'direction')})
        contract.name(row['sample_metric'])
        if row['sample_kind'] not in ('binary', 'numeric'):
            raise ValueError('逐题分值的类型不支持')


def verify_protocol(value):
    measurement = value['measurement']
    evaluator = measurement['evaluator']
    p = value['protocol']
    contract.object_fields(p, PROTOCOL_FIELDS)
    for field in ('dataset_revision', 'task_name', 'task_revision', 'split'):
        if not isinstance(p[field], str) or not p[field].strip():
            raise ValueError('协议身份缺失：' + field)
    tasks = measurement['tasks']
    if p['task_name'] != p['dataset']:
        raise ValueError('质量评测仅支持注册名称匹配的原生 benchmark')
    if ({t['benchmark'] for t in tasks} != {p['dataset']}
            or [t['subset'] for t in tasks] != p['subsets']
            or p['evaluator'] != 'evalscope' or evaluator['version'] != p['evaluator_version']):
        raise ValueError('原生任务覆盖或工具版本与范围不一致')
    if len(set(p['subsets'])) != len(tasks):
        raise ValueError('原生 subset 重复')
    refs = value.get('datasets')
    if refs is not None:
        if set(refs) != set(p['subsets']):
            raise ValueError('子集数据交接不完整')
        for reference in refs.values():
            contract.reference(reference)



def run_request(value):
    contract.object_fields(value, ('schema_version', 'measurement', 'protocol', 'scores'), ('datasets', 'scoring_proof', 'expected_static_conditions'))
    if value['schema_version'] != '1.0':
        raise ValueError('质量测量请求版本不支持')
    measurement = contract.validate(value['measurement'], 'run')
    if 'baseline' in measurement or 'targets' in measurement:
        raise ValueError('独立质量测量不能发起 GPU 测量或设定目标')
    evaluator = measurement['evaluator']
    if resolve(evaluator)[0] != 'evalscope' or evaluator.get('version', 'latest') == 'latest':
        raise ValueError('独立原生质量测量需要已固定版本的 EvalScope')
    verify_protocol(value)
    if 'scoring_proof' in value:
        contract.reference(value['scoring_proof'])
    if ('declaration' in value['protocol']['scoring_config']) != ('scoring_proof' in value):
        raise ValueError('自定义评分规则必须绑定可核对的实际 adapter 与配置')
    if not isinstance(value['scores'], list) or not value['scores']:
        raise ValueError('没有请求原生评分')
    verify_specs(value)
    return value


def validate(value, action):
    if action == 'quality-run':
        return run_request(value)
    contract.object_fields(value, ('source',), ('schema_version', 'expected'))
    if value.get('schema_version', '1.0') != '1.0':
        raise ValueError('不支持的质量分析版本')
    contract.reference(value['source'])
    if 'expected' in value:
        run_request(value['expected'])
    return value
