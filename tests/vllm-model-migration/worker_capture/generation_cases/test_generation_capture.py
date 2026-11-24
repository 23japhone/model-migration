"""Generation evidence uses the bytes and token rows actually observed."""
import json
from pathlib import Path
import sys
import unittest

VERIFY = next((Path(__file__).resolve().parents[4] / 'skills').rglob('verify-agent/scripts/verify.py')).parent
sys.path.insert(0, str(VERIFY))


class GenerationTests(unittest.TestCase):
    def test_stream_token_ranges_are_not_event_numbers(self):
        from verification.runtime.capture.generation.delivery import parse_stream
        def event(choices):
            return 'data: ' + json.dumps(dict(id='r', model='q', choices=choices)) + '\r\n\r\n'
        role = dict(index=0, delta={'role': 'assistant'})
        tokens = dict(index=0, token_ids=[4, 4], delta={'content': 'xx'}, finish_reason='length')
        data = (event([role]) + event([tokens]) + event([])).encode()
        result = parse_stream(data + b'data: [DONE]\n\n')
        self.assertEqual(result['choices'][0]['token_ids'], [4, 4])
        self.assertEqual([e['output_indices'] for e in result['delivery']['events']], [[], [0, 1], []])
        self.assertFalse(parse_stream(data)['delivery']['complete'])
        with self.assertRaises(ValueError):
            parse_stream(data + b'data: [DONE]\n\n' + event([]).encode())

    def test_random_decision_preserves_non_argmax(self):
        from verification.runtime.capture.generation.sampling import decision
        import math
        self.assertFalse(decision([math.log(.8), math.log(.2)], [.8, .2], 1)['is_argmax'])
        with self.assertRaises(ValueError):
            decision([0, -float('inf')], [1., 0.], 1)
        with self.assertRaises(ValueError):
            decision([2., 1.], [.1, .9], 1)

    def test_image_placeholder_and_mask_must_agree(self):
        from verification.runtime.capture.generation.vision import fusion_positions
        feature = {'offset': 4, 'length': 3, 'image_index': 0, 'identifier': 'i'}
        self.assertEqual(fusion_positions(feature, [5, 6, 7], [True, True, False]), [5, 6])
        with self.assertRaises(ValueError):
            fusion_positions(feature, [3, 4, 5], [True, True, False])


    def test_invalid_tail_keeps_only_proven_delivery_prefix(self):
        from verification.runtime.capture.generation.delivery import verified_prefix
        from verification.runtime.capture.generation.receipt import link_delivery
        chunk = {'id':'a','model':'m','choices':[{'index':0,'token_ids':[3,3]}]}
        raw = b'data: '+json.dumps(chunk).encode()+b'\n\ndata: bad\n\n'
        value = verified_prefix(raw,'invalid JSON')
        errors = []
        delivery = link_delivery(value, [{'output_index':0,'token_id':3,'forward_id':'a'},
                                         {'output_index':1,'token_id':3,'forward_id':'b'}], errors)
        self.assertFalse(delivery['complete'])
        self.assertEqual(delivery['events'][0]['forward_ids'], ['a', 'b'])
        self.assertEqual(errors, ['invalid JSON'])
        errors = []
        delivery = link_delivery(value, [{'output_index':0,'token_id':8,'forward_id':'c'}],errors)
        self.assertEqual(delivery['events'][0]['forward_ids'], [])
        self.assertEqual(delivery['token_alignment'],'incomplete')

    def test_matching_flat_positions_do_not_mask_disagreeing_rope_axes(self):
        from verification.runtime.capture.parallel.receipt import compare_position_axes
        members = [{'status':'complete','errors':[],'position_axes':[[4],[5],[axis]]} for axis in (5, 6)]
        compare_position_axes(members)
        self.assertTrue(all(m['status']=='incomplete' and 'M-RoPE' in m['errors'][0] for m in members))
