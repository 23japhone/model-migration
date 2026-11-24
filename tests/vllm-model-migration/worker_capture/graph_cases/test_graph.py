"""图模式入口、捕图与真实重放的边界。"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[4]
VERIFY = ROOT / 'skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/scripts'
sys.path.insert(0, str(VERIFY))
from verification.runtime.capture import contract


class GraphTests(unittest.TestCase):
    def request(self, mode):
        return {'worker_capture': {'adapter': 'msprobe_pytorch_aclgraph', 'output_root': '/capture'},
                'npu': {'devices': [6]}, 'service': {'ownership': 'owned', 'command': [
                    'vllm', 'serve', '/model', '--compilation-config', '{"cudagraph_mode":"'+mode+'"}',
                    '--dtype', 'bfloat16', '--no-async-scheduling', '--no-enable-prefix-caching',
                    '--max-num-seqs', '1']}}

    def test_graph_modes_require_single_synchronous_worker(self):
        for mode in ('FULL', 'PIECEWISE'):
            request = self.request(mode)
            contract.validate(request)
            contract.check_service(request)
        request['service']['command'].append('--enforce-eager')
        with self.assertRaises(ValueError):
            contract.check_service(request)
        with self.assertRaises(ValueError):
            contract.check_service(self.request('FULL_DECODE_ONLY'))

    def test_parse_native_call_indices_without_confusing_layer_numbers(self):
        from verification.runtime.capture.aclgraph.native import call_info
        module = call_info('Module.model.layers.31.LlamaDecoderLayer.2.input_kwargs.hidden_states.pt')
        self.assertEqual((module['call'],module['occurrence'],module['io']),
                         ('Module.model.layers.31.LlamaDecoderLayer',2,'input_kwargs'))
        api = call_info('Aten.mm.17.input.0.pt')
        self.assertEqual((api['call'],api['occurrence']),('Aten.mm',17))

    def test_only_successful_native_replays_count(self):
        from verification.runtime.capture.adapters.aclgraph.replay import ReplayLog
        log = ReplayLog('FULL')
        class Graph:
            def replay(self):
                if getattr(self, 'fail', False):
                    raise RuntimeError('device failure')
        graph = Graph()
        log.capture(graph, 'wrapper-1', 'descriptor-1', 'FULL')
        log.start()
        log.replayed(graph)
        first = log.finish()
        log.start()
        log.replayed(graph)
        second = log.finish()
        self.assertEqual(first['replays'][0]['graph_id'], second['replays'][0]['graph_id'])
        self.assertEqual([first['replays'][0]['ordinal'], second['replays'][0]['ordinal']], [1, 2])
        log.start()
        self.assertTrue(log.finish()['errors'])

    def test_untargeted_replay_does_not_enter_worker_forward(self):
        from verification.runtime.capture.adapters.aclgraph.replay import ReplayLog
        class Graph:
            pass
        log = ReplayLog('FULL')
        graph = Graph()
        log.capture(graph, 'wrapper', 'one', 'FULL')
        log.start()
        log.replayed(graph, record=False)
        log.replayed(graph)
        result = log.finish()
        self.assertFalse(result['errors'])
        self.assertEqual([e['ordinal'] for e in result['replays']], [2])

    def test_missing_first_piece_is_detected_on_first_request(self):
        from verification.runtime.capture.adapters.aclgraph.replay import ReplayLog
        class Graph:
            pass
        log = ReplayLog('PIECEWISE')
        first, last = Graph(), Graph()
        with log.calibration():
            log.capture(first, 'a', 'descriptor', 'PIECEWISE')
            log.capture(last, 'b', 'descriptor', 'PIECEWISE')
        log.start()
        log.replayed(last)
        self.assertTrue(log.finish()['errors'])
        log.start()
        for graph in (first, last):
            log.replayed(graph)
        self.assertEqual(log.finish()['errors'], [])

if __name__ == '__main__':
    unittest.main()
