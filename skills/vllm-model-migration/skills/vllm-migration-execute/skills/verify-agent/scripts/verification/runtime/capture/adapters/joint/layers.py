"""一次物理层调用共用一组挂点，成员分别扣减限额并保存原值。"""

from contextlib import ExitStack
from functools import wraps
from time import perf_counter

from ..decode_cache.events import group_for
from ..decode_cache.layer_capture import allocated_layers, floating_tensor, schedule_errors
from ..decode_cache.snapshots import Snapshots, SnapshotBudget, observer_only
from . import attention, gdn, mla
from .split import Member

BIND = {'attention': attention.bind, 'gdn': gdn.bind, 'mla': mla.bind}


class Allowance(SnapshotBudget):
    def __init__(self, physical, member):
        self.physical, self.member = physical, member

    @property
    def remaining(self):
        return min(self.physical.remaining, self.member.remaining)

    @remaining.setter
    def remaining(self, value):
        spent = self.remaining - value
        self.physical.remaining -= spent
        self.member.remaining -= spent


def boundary(store, name, value, row, total, errors):
    try:
        tensor = floating_tensor(value)
        if tensor is None or tensor.ndim != 2 or tensor.shape[0] != total:
            raise ValueError('已适配文本层的有效激活布局不符')
        start, end = row['token_range']
        store.boundary(name, tensor[start:end])
    except Exception as error:
        errors.append(name + ': ' + str(error))


def bind_states(stack, rec, desc, members, metas, invocation):
    for number, state in enumerate(desc['states']):
        items, errors, ops = [], [], []
        try:
            for row, step, store, record in members:
                state_store = store.scope('state-' + str(number))
                entry = {**state, 'event': {}, 'errors': [], 'ops': ops, 'snapshots': state_store.files}
                record['states'].append(entry)
                item = Member(row, step, group_for(step, rec.runner, state['address']), state_store, entry)
                item.event = entry['event']
                items.append(item)
            if state['module'] in rec.backend_errors:
                raise ValueError(rec.backend_errors[state['module']])
            BIND[state['kind']](
                stack, rec, rec.layer_modules[state['module']], metas[state['address']], items, ops, errors
            )
        except Exception as error:
            errors.append(str(error))
        invocation.append(errors)


def observe_block(stack, rec, desc, contexts, metas, total):
    block = rec.layer_modules[desc['path']]
    original = block.forward

    @wraps(original)
    def observed(*args, **kwargs):
        current, shared_errors = [], []
        with ExitStack() as hooks:
            with observer_only():
                for row, step, store, layer in contexts:
                    inv = {'errors': [], 'states': [], 'occurrence': len(layer['invocations'])}
                    layer['invocations'].append(inv)
                    try:
                        target = store.scope('call-' + str(inv['occurrence']))
                        inv['boundary'] = target.files
                        boundary(
                            target, 'input', kwargs.get('hidden_states', (args, kwargs)), row, total, inv['errors']
                        )
                        current.append((row, step, target, inv))
                    except Exception as error:
                        inv['errors'].append(str(error))
                if len(current) == len(contexts):
                    bind_states(hooks, rec, desc, current, metas, shared_errors)
            result = original(*args, **kwargs)
            with observer_only():
                for row, _, target, inv in current:
                    boundary(target, 'output', result, row, total, inv['errors'])
        for _, _, _, inv in current:
            inv['errors'].extend(err for errors in shared_errors for err in errors)
        return result

    if 'forward' in vars(block):
        stack.callback(setattr, block, 'forward', original)
    else:
        stack.callback(delattr, block, 'forward')
    block.forward = observed


def install(stack, rec, plan, rows, frame):
    from vllm.forward_context import get_forward_context

    start = perf_counter()
    metas = get_forward_context().attn_metadata
    group = plan['group_id']
    if getattr(rec, 'joint_group', None) != group:
        rec.joint_group, rec.joint_budgets = group, {}
    physical = SnapshotBudget(rec.cache_budget)
    contexts = []
    frame['cache'] = {}
    for row in rows:
        trace = row['trace_id']
        budget = rec.joint_budgets.setdefault(
            trace, SnapshotBudget(plan['cache_capture']['max_request_snapshot_bytes'])
        )
        folder = rec.folder / ('member-' + trace) / ('cache-' + frame['forward_id'])
        folder.mkdir(mode=0o700)
        root = Snapshots(folder, Allowance(physical, budget), rec.cache_watch)
        layers = frame['cache'][trace] = []
        step = plan['requests'][row['internal_id']]
        problems = frame.setdefault('member_errors', {}).setdefault(trace, [])
        owners = allocated_layers(rec.architecture['layers'], step, problems)
        contexts.append((row, step, root, layers, owners))
    for desc in rec.architecture['layers']:
        target = []
        for row, step, root, layers, owners in contexts:
            layer = {
                'index': desc['index'],
                'path': desc['path'],
                'kind': desc['kind'],
                'invocations': [],
                'errors': schedule_errors(desc, owners),
            }
            layers.append(layer)
            target.append((row, step, root.scope('layer-' + str(desc['index'])), layer))
        if desc['path'] in frame.get('signatures', {}):
            observe_block(stack, rec, desc, target, metas, rows[-1]['token_range'][1])
        else:
            for _, _, _, layer in target:
                layer['errors'].append('该文本层尚未验证成员 Token 轴')

    def finish():
        frame.update(
            snapshot_bytes=rec.cache_budget - physical.remaining, cache_window_seconds=perf_counter() - start
        )

    stack.callback(finish)
