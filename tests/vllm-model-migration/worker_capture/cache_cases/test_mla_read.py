"""通过公开查询核对 MLA 槽位、原始文件及同层局部损坏。"""
import json
from pathlib import Path
import sys
from unittest import TestCase, skipUnless
from unittest.mock import patch
from importlib.util import find_spec
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from worker_capture import native_fixture as native, test_runtime_closure as closure
from worker_capture.session_fixture import write

sys.path.insert(0, str(closure.VERIFY.parent))

BLOCK = 'model.layers.0'
PARENT = BLOCK + '.self_attn'
DESCRIPTOR = {'kind': 'mla', 'backend': 'vllm_ascend.attention.mla_v1.AscendMLAImpl',
              'address': BLOCK + '.attn', 'module': PARENT + '.mla_attn', 'msprobe_module': PARENT}


def collect_frame(folder, frame):
    if frame['output_index'] != 1:
        return
    import torch
    from .mla_fixture import run
    from verification.runtime.capture.adapters.decode_cache.snapshots import Snapshots
    fid = frame['forward_id']
    call_dir = folder / ('cache-' + fid) / 'layer-0/call-0'
    files_dir = call_dir / 'state-0'
    files_dir.mkdir(parents=True)
    event, files, observation = run(files_dir, 130, 1, False)
    identity = uuid4().hex
    group = {'layers': [DESCRIPTOR['address']], 'page_size': 128, 'blocks': [3, 1]}
    schedule = {'schedule_id': identity, 'internal_id': frame['internal_id'], 'output_index': 1,
                'position': 130, 'groups': [group]}
    write(folder / ('schedule-' + identity + '.json'), schedule)
    boundary = Snapshots(call_dir, 100, None)
    for side in ('input', 'output'):
        boundary.save(side, torch.ones(1))
    state = {**DESCRIPTOR, **observation, 'snapshots': files, 'event': event}
    call = {'occurrence': 0, 'boundary': boundary.files, 'states': [state], 'errors': []}
    layer = {'path': BLOCK, 'index': 0, 'kind': 'mla', 'status': 'complete', 'invocations': [call], 'errors': []}
    cache = {k: frame[k] for k in ('forward_id', 'internal_id', 'output_index', 'positions', 'step')}
    frame['decode_cache'] = {**cache, 'format': 2, 'rank': 0, 'schedule_id': identity, 'schedule': schedule,
                             'layers': [layer], 'status': 'complete', 'ops': [], 'errors': []}


@skipUnless(find_spec('torch'), '需要 CPU PyTorch')
class MLARead(TestCase):
    setUp = closure.RuntimeClosure.setUp
    start = closure.RuntimeClosure.start
    read = closure.RuntimeClosure.read
    invoke = closure.RuntimeClosure.invoke
    operation = closure.RuntimeClosure.operation

    def test_output_index_survives_one_bad_component(self):
        self.start(cache=True)
        ready_path = self.runtime / 'worker.json'
        ready = json.loads(ready_path.read_bytes())
        desc = {'index': 0, 'path': BLOCK, 'kind': 'mla', 'states': [DESCRIPTOR]}
        ready.update(architecture={'layers': [desc], 'counts': {'mla': 1}}, layers=[BLOCK])
        write(ready_path, ready)
        self.enterContext(patch.object(native, 'MODULES', [BLOCK, PARENT]))
        respond = native.respond
        self.enterContext(patch.object(native, 'respond', lambda root, trace, tokens:
                                       respond(root, trace, tokens, [11, 22]*65, collect_frame)))
        _, seal = self.invoke(cache_output_index=1)
        receipt = self.operation(seal)['receipt']
        latent = self.read(receipt, layer_index=0, cache_snapshot='mla.latent.after')
        self.assertEqual(latent['positions'], [130])
        call = latent['decode_cache']['layers'][0]['invocations'][0]['states'][0]
        via_module = self.read(receipt, layer=DESCRIPTOR['module'], cache_snapshot='mla.latent.after')
        self.assertEqual(via_module['cache_snapshot'], latent['cache_snapshot'])
        self.assertIn(PARENT, call['msprobe_call'])
        self.assertEqual(call['event']['decode']['kv_length'], 131)
        self.assertEqual(call['read_ranges'][0]['physical_slot_ranges'], [[384, 512], [128, 131]])
        native_data = Path(latent['native_metadata'][0]['path'])
        native_data.write_bytes(b'corrupted')
        healthy = self.read(receipt, layer_index=0, cache_snapshot='mla.latent.after')
        self.assertEqual(healthy['native_recheck']['status'], 'incomplete')
        self.assertEqual(healthy['calls'], [])
        self.assertEqual(healthy['cache_snapshot'], latent['cache_snapshot'])
        Path(latent['cache_snapshot']['path']).write_bytes(bytes(latent['cache_snapshot']['bytes']))
        rope = self.read(receipt, layer_index=0, cache_snapshot='mla.rope.after')
        self.assertEqual(rope['forward_id'], latent['forward_id'])
        self.assertEqual(rope['cache_snapshot']['values'], [2, 3])
        self.assertEqual(rope['decode_cache']['status'], 'incomplete')
        self.assertIn('mla.latent.after', str(rope['decode_cache']['errors']))
