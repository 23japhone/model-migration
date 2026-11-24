"""分别解释实际收到的写槽、FIA 读取范围、卷积状态行及循环状态行。"""
from .plan import attention_plan, slot_ranges, state_row
from .snapshots import layout


def group_for(plan, runner, name):
    for gid, group in enumerate(plan['groups']):
        if name in group['layers']:
            actual = runner.kv_cache_config.kv_cache_groups[gid]
            if name not in actual.layer_names or actual.kv_cache_spec.block_size != group['page_size']:
                raise ValueError('worker 与 scheduler 的层分组或页大小不符')
            return {**group, 'group_id': gid, 'layer': name}
    raise ValueError('调度分配中缺少目标层 ' + name)


def one(values):
    if hasattr(values, 'tolist'):
        values = values.tolist()
    value, = values
    return int(value)


def slot_view(cache, slot, size):
    block, offset = divmod(slot, size)
    if slot < 0 or cache.ndim != 4 or cache.shape[1] != size or block >= cache.shape[0]:
        raise ValueError('K/V 写槽超出实际缓存布局')
    return cache[block, offset]


class Attention:
    def __init__(self, snapshots, group, position, kernel):
        self.snapshots = snapshots
        self.kernel = kernel
        self.position = position
        self.event = {**group, **attention_plan(group['blocks'], group['page_size'], kernel, position),
                      'kernel_block_size': kernel}
        self.views = {}
        self.handles = {}
        self.caches = {}

    def before_write(self, args):
        slot = one(args['slot_mapping'])
        self.event.update(write_slot=slot, write_plan_matches=slot == self.event['expected_slot'])
        for name in ('key', 'value'):
            cache = args[name + '_cache']
            view = slot_view(cache, slot, self.kernel)
            incoming = args[name]
            if incoming.shape[0] != 1 or incoming[0].shape != view.shape or incoming.dtype != view.dtype:
                raise ValueError('本路径要求单 Token、相同 dtype 的普通 K/V 写入')
            self.caches[name] = cache
            self.watch(name, view)
            expected = self.event['expected_slot']
            if expected != slot and 0 <= expected < cache.shape[0] * self.kernel:
                self.watch('expected_' + name, slot_view(cache, expected, self.kernel))
            self.snapshots.save(name + '.input', incoming[0])

    def watch(self, name, view):
        self.views[name] = view
        self.handles[name] = self.snapshots.before(name, view, False)

    def after_write(self):
        for name, view in self.views.items():
            change = self.snapshots.after(name, view, self.handles[name], 'reshape_and_cache')
            if name in self.caches:
                change['matches_input'] = change['digest_after'] == self.snapshots.files[name + '.input']['sha256']
            self.event[name] = change

    def read(self, args):
        size = args['block_size']
        length = one(args['actual_seq_lengths_kv'])
        if size != self.kernel or length <= 0 or args['sparse_mode'] != 3 or one(args['actual_seq_lengths']) != 1:
            raise ValueError('只适配单 Token、标准因果 FIA 读取')
        table = args['block_table']
        count = (length + size - 1) // size
        if table.ndim != 2 or table.shape[0] != 1 or count > table.shape[1]:
            raise ValueError('FIA 实际请求行或有效块数不符')
        blocks = table[0, :count].tolist()
        self.check_read_views(args, blocks)
        mask = args.get('atten_mask')
        self.event.update(read_op='npu_fused_infer_attention_score', read_source='paged_kv', read_blocks=blocks,
                          kv_length=length, query_length=1, sparse_mode=3, input_layout=args['input_layout'],
                          mask=layout(mask) if mask is not None else None, read_slot_ranges=slot_ranges(blocks, size, length),
                          read_plan_matches=blocks == self.event['expected_read_blocks'] and length == self.position+1)

    def check_read_views(self, args, blocks):
        if set(self.caches) != {'key', 'value'}:
            raise ValueError('FIA 前缺少 K/V 写入绑定')
        for name, cache in self.caches.items():
            actual = args[name]
            expected = cache.view(cache.shape[0], self.kernel, -1)
            if layout(actual) != layout(expected):
                raise ValueError('FIA 读取与已绑定 KV 缓存不同')
            if min(blocks) < 0 or max(blocks) >= cache.shape[0]:
                raise ValueError('读取块超出缓存')


class GDN:
    def __init__(self, snapshots, group, position, mode, metadata, caches):
        self.snapshots = snapshots
        self.caches = caches
        self.event = {**group, 'mode': mode, 'metadata_row': one(metadata.non_spec_state_indices_tensor),
                      'expected_row': state_row(group['blocks'], group['page_size'], position, mode)}
        self.handles = {}

    def before(self, name, args):
        if name == 'conv':
            row = one(args['cache_indices_opt'])
            cache = args['conv_state']
            extra = {'run_mode': args['run_mode'], 'query_start_loc': list(args['query_start_loc_opt'])}
            if extra != {'run_mode': 1, 'query_start_loc': [0, 1]}:
                raise ValueError('卷积调用不是纯单 Token decode')
            expected_cache = self.caches[0]
        else:
            row = one(args['ssm_state_indices'])
            cache = args['state']
            extra = {'actual_seq_lengths': args['actual_seq_lengths'].tolist()}
            if extra['actual_seq_lengths'] != [0, 1]:
                raise ValueError('循环更新不是纯单 Token decode')
            expected_cache = self.caches[1]
        if cache is not expected_cache or not 0 <= row < cache.shape[0]:
            raise ValueError('GDN 缓存或状态行不符')
        self.event[name] = {**extra, 'row': row, 'plan_matches': row == self.event['expected_row'],
                            'metadata_matches': row == self.event['metadata_row']}
        view = cache[row]
        self.handles[name] = view, self.snapshots.before(name, view, True)
        expected = self.event['expected_row']
        if expected != row and 0 <= expected < cache.shape[0]:
            other = cache[expected]
            self.handles['expected_' + name] = other, self.snapshots.before('expected_' + name, other, True)

    def after(self, name):
        view, handle = self.handles[name]
        self.event[name].update(self.snapshots.after(name, view, handle, name))
        alternate = 'expected_' + name
        if alternate in self.handles:
            view, handle = self.handles[alternate]
            self.event[alternate] = self.snapshots.after(alternate, view, handle, name)
