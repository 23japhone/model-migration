"""相同 Token、不同物理行上的注意力、GDN 和 MLA 观察。"""

from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

import torch

sys.path.insert(
    0, str(next((Path(__file__).resolve().parents[4] / 'skills').rglob('verify-agent/scripts/verify.py')).parent)
)
from verification.runtime.capture.adapters.joint.attention import AttentionBatch
from verification.runtime.capture.adapters.joint.gdn import GatedBatch
from verification.runtime.capture.adapters.joint.layers import Allowance
from verification.runtime.capture.adapters.joint.mla import Partition
from verification.runtime.capture.adapters.joint.split import Member
from verification.runtime.capture.adapters.decode_cache.snapshots import SnapshotBudget


class Store:
    def __init__(self):
        self.files = {}

    def save(self, name, value):
        self.files[name] = value.clone()

    boundary = save

    def before(self, name, view, keep_value):
        self.save(name + '.before', view)
        return None

    def after(self, name, view, handle, op):
        self.save(name + '.after', view)
        return {}


def member(index, begin, end, block, position=0):
    row = dict(batch_row=index, token_range=[begin, end])
    step = dict(position=position, count=end - begin)
    item = Member(row, step, dict(blocks=[block], page_size=4), Store(), dict(errors=[]))
    item.event = {}
    return item


class BackendRows(unittest.TestCase):
    def test_multi_slot_write_and_causal_read(self):
        a, b = member(0, 0, 1, 1, 2), member(1, 1, 3, 0)
        key = torch.tensor([[[2.0]], [[3.0]], [[5.0]]])
        value = key + 7
        slots = torch.tensor([6, 0, 1])
        cache = torch.zeros(2, 4, 1, 1)
        other = cache.clone()
        obs = AttentionBatch([a, b], 4, NS(attn_state=NS(name='ChunkedPrefill'), slot_mapping=slots))
        args = dict(slot_mapping=slots, key=key, value=value, key_cache=cache, value_cache=other)
        obs.before_write(args)
        cache.view(-1, 1, 1)[slots] = key
        other.view(-1, 1, 1)[slots] = value
        obs.after_write()
        obs.read(
            dict(
                actual_seq_lengths=[1, 3],
                actual_seq_lengths_kv=[3, 2],
                block_table=torch.tensor([[1], [0]]),
                block_size=4,
                key=cache.view(2, 4, -1),
                value=other.view(2, 4, -1),
                sparse_mode=3,
                input_layout='TND',
            )
        )
        self.assertEqual((a.errors, b.errors), ([], []))
        self.assertTrue(torch.equal(a.store.files['key.after'], key[:1]))
        self.assertTrue(torch.equal(b.store.files['value.after'], value[1:]))
        self.assertEqual(b.event['causal_end_exclusive'], [1, 2])
        with self.assertRaisesRegex(ValueError, '写槽'):
            obs.before_write({**args, 'slot_mapping': torch.tensor([6, 2, 1])})

    def test_mixed_gdn_chunk_preserves_each_members_state(self):
        a, b = member(0, 0, 1, 1, 3), member(1, 1, 3, 0)
        caches = (torch.tensor([[20.], [31.]]), torch.arange(8.).reshape(2, 2, 2))
        rec = NS(runner=NS(cache_config=NS(mamba_cache_mode='none')))
        meta = NS(non_spec_state_indices_tensor=torch.tensor([1, 0]), has_initial_state=torch.tensor([True, False]))
        batch = GatedBatch(rec, NS(kv_cache=caches), meta, [a, b], True)
        batch.before_conv(dict(query_start_loc_opt=[0, 1, 3], cache_indices_opt=[1, 0],
                          initial_state_mode_opt=[1, 0], conv_state=caches[0], run_mode=0))
        caches[0].add_(10)
        batch.after('conv')
        initial = torch.stack((caches[1][1].T, torch.zeros_like(caches[1][0])))
        batch.before_chunk(dict(cu_seqlens=torch.tensor([0, 1, 3]), initial_state=initial, output_final_state=True))
        batch.final = initial + torch.tensor([11., 23.])[:, None, None]
        caches[1][1].copy_(batch.final[0].T)
        caches[1][0].copy_(batch.final[1].T)
        batch.writeback()
        self.assertEqual((a.errors, b.errors), ([], []))
        self.assertEqual(a.store.files['conv.before'].item(), 31.)
        self.assertEqual(b.store.files['conv.after'].item(), 30.)
        self.assertTrue(torch.equal(a.store.files['ssm.before'], a.store.files['ssm.initial']))
        self.assertFalse(torch.count_nonzero(b.store.files['ssm.initial']))
        self.assertTrue(torch.equal(b.store.files['ssm.after'], caches[1][0]))

    def test_recurrent_lengths_are_counts_and_rows_are_independent(self):
        a, b = member(0, 0, 1, 1), member(1, 1, 2, 0)
        recorder = GatedBatch.__new__(GatedBatch)
        recorder.items, recorder.offsets = [a, b], [0, 1, 2]
        for item, index in ((a, 8), (b, 4)):
            item.event.update(expected_row=index, metadata_row=index)
            item.capture = NS(before=lambda name, kw: self.assertEqual(kw['actual_seq_lengths'].tolist(), [0, 1]))
        recorder.before(
            'ssm', dict(actual_seq_lengths=torch.tensor([0, 1, 1]), ssm_state_indices=torch.tensor([8, 4]))
        )
        self.assertEqual((a.errors, b.errors), ([], []))
        recorder.before(
            'ssm', dict(actual_seq_lengths=torch.tensor([0, 1, 1]), ssm_state_indices=torch.tensor([8, 8]))
        )
        self.assertFalse(a.errors)
        self.assertTrue(b.errors)

    def test_mla_prefill_subbatch_restarts_at_its_actual_offset(self):
        item = member(1, 1, 3, 2)
        item.record['ops'] = []
        item.event.update(expected_slots=[8, 9])
        observed = []
        item.capture = NS(expected_meta_slots=[8, 9], write=lambda args, kw: observed.append(args[0].tolist()))
        part = Partition.__new__(Partition)
        part.items, part.base = [item], 1
        part.write([torch.tensor([31, 47]), None, None, None, torch.tensor([8, 9])], {})
        self.assertEqual(observed, [[31, 47]])
        part.write([torch.tensor([31, 47]), None, None, None, torch.tensor([0, 1])], {})
        self.assertEqual(len(observed), 1)
        self.assertTrue(item.errors)

    def test_member_limit_does_not_drain_peer_budget(self):
        shared = SnapshotBudget(48)
        a = Allowance(shared, SnapshotBudget(16))
        b = Allowance(shared, SnapshotBudget(32))
        a.remaining -= 16
        self.assertEqual((a.remaining, b.remaining, shared.remaining), (0, 32, 32))
