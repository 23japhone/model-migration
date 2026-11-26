"""从同一会话的原件验证复用、缺项和来源筛选。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from worker_capture.test_runtime_closure import VERIFY
from worker_capture.prefix_cases.evidence_fixture import pair, save
sys.path.insert(0, str(VERIFY.parent))
from verification.runtime.capture.prefix import receipt, reader


class SourceTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.folder, self.timeline, self.prompt = pair(root)

    def seal(self):
        return receipt.reconcile(self.folder, self.timeline, self.prompt, {'observe_values':True})

    def query(self, proof, pos, **filters):
        return reader.lookup({'request_id':'B','prefix_reuse':proof},{'prompt_position':pos,**filters})

    def test_real_positions_and_terminal_checkpoint(self):
        proof = self.seal()
        self.assertEqual(proof['errors'], [])
        self.assertEqual(len(proof['components']), 10)
        for i in range(4):
            sources = self.query(proof, i)['sources']
            self.assertEqual({s['forward_id'] for s in sources if s['layer_index']!=1},{'A0' if i<2 else 'A2'})
            self.assertEqual({s['forward_id'] for s in sources if s['layer_index']==1},{'A2'})

    def test_missing_one_source_after_sealing(self):
        proof = self.seal()
        Path(proof['components'][0]['source_file']['path']).unlink()
        result = self.query(proof, 0)
        self.assertEqual(result['status'],'incomplete')
        self.assertTrue(self.query(proof,0,layer_index=2,cache_snapshot='mla.rope.after')['sources'])
        self.assertTrue(any('层 0 key' in error for error in result['errors']))

    def test_external_source_and_mismatched_bytes(self):
        file = self.folder/'B4key.bin'
        file.write_bytes(b'x'+file.read_bytes()[1:])
        self.assertTrue(any('摘要变化' in e for e in self.seal()['errors']))
        save(self.folder.parent/'lease.json', {'sealed':[]})
        proof=self.seal()
        self.assertEqual(proof['hit_tokens'],4)
        self.assertTrue(proof['errors'])
        self.assertFalse(proof['components'])

    def test_partial_checkpoint_is_not_a_verified_layer(self):
        row_path = Path(self.timeline[0]['row']['path'])
        row = json.loads(row_path.read_bytes())
        gdn = row['decode_cache']['layers'][1]['invocations'][0]['states'][0]
        gdn['event']['prefix_state']['ssm']['copy_ranges'] = [[0,0,2]]
        self.timeline[0]['row'] = save(row_path,row)
        proof = self.seal()
        self.assertEqual(proof['verified_hit_tokens'],0)
        self.assertTrue(any('ssm' in e and '部分字节' in e for e in proof['errors']))
        self.assertTrue(self.query(proof,2,layer_index=1,cache_snapshot='conv.before')['sources'])

    def test_stale_residency(self):
        row_path=Path(self.timeline[0]['row']['path'])
        value=json.loads(row_path.read_bytes())
        value['decode_cache']['schedule']['prefix_cache']['lookup']['groups'][0][0]['generation']+=1
        self.timeline[0]['row']=save(row_path,value)
        proof=self.seal()
        self.assertTrue(proof['errors'])
        self.assertFalse(any(x['layer_index']==0 for x in proof['components']))
