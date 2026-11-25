"""跨真实模型家族验证结构发现，不下载模型权重。"""
from importlib.util import find_spec
from pathlib import Path
import sys
import unittest

VERIFY = next((Path(__file__).resolve().parents[4] / 'skills').rglob('verify-agent/scripts/verify.py'))
sys.path.insert(0, str(VERIFY.parent))


@unittest.skipUnless(find_spec('transformers') and find_spec('torch'), '需要本地 Transformers 和 PyTorch')
class ArchitectureTests(unittest.TestCase):
    def test_static_text_and_vision_declarations(self):
        precheck = VERIFY.parents[2] / 'vllm-model-precheck' / 'scripts'
        sys.path.insert(0, str(precheck))
        self.addCleanup(sys.path.remove, str(precheck))
        from model_precheck.model import declared_stacks
        rows = declared_stacks({'vision_config': {'depth': 4},
                                'text_config': {'num_hidden_layers': 2, 'layer_types': ['gdn','attention']}})
        self.assertEqual([r['count'] for r in rows], [4,2])
        self.assertEqual(rows[0]['status'], 'runtime_resolution_required')
        self.assertIn('1  attention', rows[1]['diagram'])

    def test_different_layer_containers_with_real_decoders(self):
        from transformers import GPT2Config, GPT2LMHeadModel, LlamaConfig, LlamaForCausalLM
        from verification.runtime.capture.adapters.architecture import discover
        llama = LlamaForCausalLM(LlamaConfig(num_hidden_layers=3, num_attention_heads=2,
            num_key_value_heads=1, hidden_size=16, intermediate_size=32, vocab_size=128))
        gpt = GPT2LMHeadModel(GPT2Config(n_layer=2, n_head=2, n_embd=16, n_positions=24, vocab_size=128))
        for model, stack, count in ((llama, 'model.layers', 3), (gpt, 'transformer.h', 2)):
            graph = discover(model, model.config)
            self.assertEqual(graph['stack'], stack)
            self.assertEqual([b['path'] for b in graph['layers']], [f'{stack}.{i}' for i in range(count)])
            self.assertIn(stack, graph['diagram'])
            self.assertTrue(all(b['class'] and b['children'] for b in graph['layers']))
