"""从两端实际接口清单匹配同构调用，或采用已复核的异构映射。"""
from alignment.evidence import bound, ref, safe, write
from token_stage.bundles import DATA_ERRORS
from token_stage.selection import case_key
from .cpu_scope import derive
from . import versions


class Sources:
    def __init__(self, references):
        self.items = []
        self.issues = []
        for source in references:
            try:
                value = bound(source)
                if value.get('format') in ('tensor-bundle-v1', 'tensor-plan-v1'):
                    case_key(value.get('case'))
                if not any(old == source for old, _ in self.items):
                    self.items.append((source, value))
            except DATA_ERRORS as exc:
                self.issues.append({'source': source, 'reason': safe(exc)})

    def find(self, fmt, **attrs):
        return [(source, value) for source, value in self.items
                if value.get('format') == fmt and all(value.get(k) == v for k, v in attrs.items())]

    def bundle(self, plan_ref, plan, side):
        matches = []
        for source, value in self.find('tensor-bundle-v1', plan_sha256=plan_ref['sha256'], hardware=side):
            fields = [key for key in ('case', 'position', 'model_identity') if value.get(key) != plan[key]]
            if fields:
                self.issues.append({'source': source, 'reason': side + ' 张量包与采集计划不符：' + '、'.join(fields)})
            else:
                matches.append((source, value))
        return unique(matches, side + ' 张量包')


def unique(entries, name):
    if len(entries) > 1:
        raise ValueError(name + '有多条匹配记录，无法唯一确定')
    return entries[0] if entries else (None, None)


def identity(value, keys):
    items = tuple(value[key] for key in keys)
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValueError('调用或槽位缺少名称')
    return items


def call_key(call):
    ident, kind = identity(call, ('id', 'kind'))
    occurrence = call['occurrence']
    if kind not in ('api', 'module') or type(occurrence) is not int or occurrence < 0:
        raise ValueError('调用类别或出现序号无效')
    return ident, kind, occurrence


def slot_key(slot):
    ident, role = identity(slot, ('id', 'role'))
    if role not in ('input','output','weight','state'):
        raise ValueError('张量槽位角色无效')
    return ident, role


def by_key(rows, function):
    if not isinstance(rows, list) or not rows:
        raise ValueError('调用或张量槽位清单为空')
    keyed = {function(item): item for item in rows}
    if len(keyed) != len(rows):
        raise ValueError('调用或语义槽位标识重复')
    return keyed


def boundary(item):
    if not isinstance(item['owner'], str):
        raise ValueError('调用归属路径无效')
    identity(item, ('name',))
    return {key:item[key] for key in ('owner','name','scalar_args','arguments_complete') if key in item}


def common_slot(key, gpu, npu):
    slot = {'id': key[0], 'role': key[1], 'gpu': gpu['path'], 'npu': npu['path']}
    sources = {name: item['fp8_scale_source'] for name, item in (('gpu',gpu),('npu',npu)) if 'fp8_scale_source' in item}
    if sources:
        slot['fp8_scale_source'] = sources
    scaled = {side: row.get('fp8_scaled', False) for side, row in (('gpu', gpu), ('npu', npu))}
    if any(scaled.values()):
        slot['fp8_scaled'] = scaled
    return slot


def common_call(gpu, npu, issues):
    gpu_bound, npu_bound = boundary(gpu), boundary(npu)
    if (gpu_bound['owner'], gpu_bound['name']) != (npu_bound['owner'], npu_bound['name']):
        return None
    left, right = [by_key(side['slots'], slot_key) for side in (gpu, npu)]
    slots = [common_slot(key, row, right[key]) for key, row in left.items() if key in right]
    unpaired = [{'missing_device': side, 'slot': key[0], 'role': key[1]}
                for side, keys in (('gpu', right.keys() - left), ('npu', left.keys() - right)) for key in sorted(keys)]
    for gap in unpaired:
        issues.append(f"{gpu['id']} 的 {gap['role']} 槽位 {gap['slot']} 缺少 {gap['missing_device']} 映射")
    if not slots:
        return None
    cpu = gpu.get('cpu_reference')
    common_cpu = {'cpu_reference': cpu} if cpu and not unpaired and cpu == npu.get('cpu_reference') else {}
    return {**common_cpu, **({'unpaired_slots': unpaired} if unpaired else {}), **{k: gpu[k] for k in ('id', 'kind', 'occurrence')},
            'gpu': gpu_bound, 'npu': npu_bound,
            'slots': slots}


def inventoried(sources, model):
    inventory = []
    for side in ('gpu', 'npu'):
        _, value = unique(sources.find('tensor-inventory-v1', hardware=side, model_identity=model), side + ' 调用清单')
        if value is None:
            return [], ['缺少 ' + side + ' 实际调用清单或已核对的双端映射']
        inventory.append(by_key(value['calls'], call_key))
    gpu, npu = inventory
    calls, issues = [], []
    for key in dict.fromkeys([*gpu, *npu]):
        item = common_call(gpu[key], npu[key], issues) if key in gpu and key in npu else None
        if item:
            calls.append(item)
        else:
            issues.append('需要核对双端调用或槽位的映射：' + str(key))
    return calls, issues


def persisted_plan(sources, target):
    relevant = sources.find('tensor-plan-v1', case=target['case'], position=target['position'],
                            model_identity=target['model_identity'])
    matches = [(source, value) for source, value in relevant if
               all(value.get(key) == item for key, item in target.items())]
    return unique(matches, '同一首异常的已有计划')


def check_calls(calls):
    keyed = by_key(calls, call_key)
    if len({row['id'] for row in keyed.values()}) != len(keyed):
        raise ValueError('计划中的调用 ID 须唯一标识一次调用')
    for call in keyed.values():
        for side in ('gpu', 'npu'):
            boundary(call[side])
        slots = call['slots']
        by_key(slots, slot_key)
        if len({slot['id'] for slot in slots}) != len(slots):
            raise ValueError('同次调用的槽位 ID 须唯一')
        for slot in slots:
            for side in ('gpu', 'npu'):
                if not isinstance(slot[side], list):
                    raise ValueError('槽位选择路径必须是列表')


def prepare(sources, target, directory):
    model = target['model_identity']
    prior_ref, prior = persisted_plan(sources, target)
    _, scope = unique(sources.find('tensor-scope-v1', model_identity=model), '已核对的双端映射')
    if scope:
        calls, issues = scope['calls'], []
    elif sources.find('tensor-inventory-v1', model_identity=model) or not prior:
        calls, issues = inventoried(sources, model)
    else:
        calls, issues = prior['calls'], prior.get('scope_uncovered', [])
    frozen = versions.pair(sources, target, scope, prior)
    gaps = versions.missing(frozen)
    if not calls or gaps:
        return None, [*issues, *gaps]
    check_calls(calls)
    plan = {**target, 'calls': [derive(call) for call in calls], 'scope_uncovered': issues}
    if frozen:
        plan['runtime_versions'] = frozen
    if prior == plan:
        return prior_ref, issues
    path = directory / 'tensor-plan.json'
    write(path, plan)
    return ref(path), issues
