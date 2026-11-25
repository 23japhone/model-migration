"""按消费者的 prompt 位置回查生产前向和对应的已核实原值。"""
from ....evidence import read_bound
from .compare import compare
from .receipt import FAILURES


def selected(part, request, pos, name):
    if not part['range'][0] <= pos < part['range'][1]:
        return False
    if 'layer_index' in request and part['layer_index'] != request['layer_index']:
        return False
    if 'layer' in request and request['layer'] not in (part['layer'], part['layer_path']):
        return False
    return not name or part['component'] == name


def verify_part(part, rows, verified):
    row_path = part['row']['path']
    if row_path not in rows:
        rows[row_path] = read_bound(part['row'])
    producer = rows[row_path]
    if producer['forward_id'] != part['forward_id'] or producer['client_request_id'] != part['request_id']:
        raise ValueError('来源前向身份不同')
    compare(part, verified)


def recheck(parts):
    good, errors, verified, rows = [], [], set(), {}
    for part in parts:
        try:
            verify_part(part, rows, verified)
            good.append(part)
        except FAILURES as error:
            errors.append(f"{part['request_id']} {part['forward_id']} 组 {part.get('group_id', '?')} "
                          f"层 {part['layer_index']} {part['component']} {part['range']}: {error}")
    return good, errors


def lookup(result, request):
    proof = result['prefix_reuse']
    pos = request['prompt_position']
    selection = request.get('cache_snapshot', '')
    if selection and not selection.endswith(('.prefix', '.before', '.after')):
        raise ValueError('复用位置可选择生产写后值或消费者前缀值')
    if 'api' in request or request.get('occurrence', 0):
        raise ValueError('复用位置的 API 调用须从生产前向读取')
    name = selection.rsplit('.', 1)[0]
    parts, errors = recheck(part for part in proof['components'] if selected(part, request, pos, name))
    errors = [*proof['errors'], *errors]
    if not parts and not errors:
        errors.append('所选位置或状态没有生产前向原值')
    return {'request_id': result['request_id'], 'prompt_position': pos, 'kind': 'reused_prefix',
            'hit_tokens': proof['hit_tokens'], 'sources': parts, 'errors': errors,
            'status': 'incomplete' if errors else 'complete'}



def timeline(result, ready):
    from .coverage import coverage
    from .receipt import COMPONENTS
    proof = dict(result['prefix_reuse'])
    good, errors = recheck(proof['components'])
    proof.update(components=good, errors=[*proof['errors'], *errors])
    proof.update(coverage(proof, ready['architecture']['layers'], COMPONENTS, result['cache_capture'].get('observe_values', True)))
    result['prefix_reuse'] = proof
    if errors:
        result.update(cache_status='incomplete', cache_errors=[*result['cache_errors'], *errors])
