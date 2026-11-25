"""配置、逐题 token 预算与旧 CLI 路由。"""
import json
import sys
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPTS = (Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/skills/vllm-precision-align'
           / 'skills/verify-agent-plus/scripts')
sys.path.insert(0, str(SCRIPTS))

from precision.cli import is_quality
from precision.quality import budget, run as native_run, runtime
from precision.quality.protocol import check_config, ref, score_only
from precision.runtime.evalscope.configuration import generation


class NativeProtocol(unittest.TestCase):
    def test_requests_for_token_probabilities_and_performance_sampling_are_rejected(self):
        for cfg in ({'collect_perf':True,'generation_config':{}},
                    {'generation_config':{'top_logprobs':0}},
                    {'generation_config':{'extra_body':{'return_token_ids':True}}}):
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                score_only(cfg)
        score_only({'collect_perf':False,'generation_config':{}})

    def test_probe_starts_with_the_supported_isolated_interpreter(self):
        with tempfile.TemporaryDirectory() as temporary:
            absent = str(Path(temporary)/'input-does-not-exist.json')
            process = subprocess.run([sys.executable, '-I', '-B', SCRIPTS/'precision/quality/probe.py',
                                      absent, absent], capture_output=True, text=True, check=False)
        self.assertIn('FileNotFoundError', process.stderr)
        self.assertNotIn('ModuleNotFoundError', process.stderr)

    def test_native_service_default_preserves_the_normal_precision_budget_requirement(self):
        cfg = {'generation_config':{'stream':False,'n':1,'retries':1},'model_args':{'max_retries':0}}
        generation(cfg, allow_native_budget=True)
        self.assertNotIn('max_tokens', cfg['generation_config'])
        with self.assertRaisesRegex(ValueError, 'max_tokens'):
            generation(cfg)
        cfg['generation_config']['max_tokens'] = -2
        with self.assertRaisesRegex(ValueError, 'max_tokens'):
            generation(cfg, allow_native_budget=True)

    def test_template_arguments_count_actual_native_messages(self):
        tokenizer = Mock()
        tokenizer.apply_chat_template.return_value = [120,44,9]
        sample = {'input':[{'role':'system','content':'sys'},{'role':'user','content':'test'}]}
        self.assertEqual(3,budget.prompt_length(sample,tokenizer,'jinja',{'enable_thinking':False}))
        args, kwargs = tokenizer.apply_chat_template.call_args
        self.assertEqual(args[0],sample['input'])
        self.assertIs(kwargs['enable_thinking'],False)
        self.assertFalse(kwargs['truncation'])
        self.assertEqual('jinja',kwargs['chat_template'])

    def test_oversized_frozen_prompt_blocks_before_inference(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder).resolve()/'sample.json'
            path.write_text(json.dumps([{'input':[{'role':'user','content':'test'}]}]))
            tokenizer = Mock()
            tokenizer.apply_chat_template.return_value = [1,2,3,4]
            payload = {'service_snapshot':{'model':{'max_model_len':10}}}
            with patch.object(budget,'tokenizer_settings',return_value=(tokenizer,None,{})):
                with self.assertRaisesRegex(ValueError,'上下文'):
                    budget.inspect({'generation_config':{'max_tokens':7}},
                                   [{'subset':'s','sample_snapshot':ref(path)}],payload)

    def test_native_prompt_and_scoring_drift(self):
        raw = {'dataset_args':{'gsm8k':{'subset_list':['default'],'eval_split':'test','few_shot_num':4}},
               'judge':{'strategy':'rule'},'generation_config':{'max_tokens':20}}
        protocol = {'dataset':'gsm8k','subsets':['default'],'split':'test','generation_config':{'max_tokens':20},
                    'prompt_config':{'request_format':'chat','few_shot_num':4},'scoring_config':{'judge':raw['judge']}}
        check_config(protocol,raw,raw)
        with self.assertRaisesRegex(ValueError,'提示'):
            check_config({**protocol,'prompt_config':{'request_format':'chat','few_shot_num':0}},raw,raw)

    def test_formal_process_directly_executes_the_frozen_sdk_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            attempt = Path(folder).resolve()
            cfg = attempt/'effective-config.json'
            cfg.write_text(json.dumps({'api_url':'http://127.0.0.1:9988/v1'}))
            with patch.object(native_run, 'execute', return_value={'returncode':0}) as launch:
                native_run.run_native({'evaluator':{'python':sys.executable}}, attempt)
            argv = launch.call_args.args[0]
            self.assertEqual(argv[:3], [sys.executable,'-I','-B'])
            self.assertEqual(argv[-2:], ['run',str(cfg)])
            self.assertEqual(json.loads(cfg.read_text())['api_url'], 'http://127.0.0.1:9988/v1')
            self.assertFalse(any(attempt.rglob('http-capture')))
            self.assertEqual(launch.call_count,1)

    def test_static_generation_checks_argv_without_platform_identity(self):
        files = {'generation_config.json':'a'*64,'tokenizer.json':'b'*64}
        value = runtime.defaults({'--override-generation-config':'{"temperature":0.2}'},files)
        self.assertEqual(value['overrides'], {'--generation-config':'auto',
                                              '--override-generation-config':{'temperature':0.2}})
        self.assertEqual(value['model_generation_files'], {'generation_config.json':'a'*64})
        identity = runtime.tokenization('{{x}}', files)
        self.assertNotEqual(identity['chat_template_sha256'],runtime.tokenization('{{y}}',files)['chat_template_sha256'])

    def test_only_explicit_extended_analyze_is_routed(self):
        self.assertFalse(is_quality('analyze',{'source':{}}))
        self.assertTrue(is_quality('analyze',{'source':{},'expected':{}}))
        self.assertTrue(is_quality('quality-run',{}))


if __name__ == '__main__':
    unittest.main()
