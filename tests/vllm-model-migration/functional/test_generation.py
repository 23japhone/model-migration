"""使用真实编译器与协议检查保护模板、预算和完整结束要求。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/vllm-migration-execute/skills/verify-agent/scripts'))


class GenerationTests(unittest.TestCase):
    def test_default_budget_does_not_cut_off_completed_long_answer(self):
        from customer_workflow.compile.deployment import cases
        from verification.protocol.cases import validate_response
        # 模拟可在129token正常结束的模型，实际请求额度决定其结束原因。
        for case in cases('arbitrary', {'prompt': 'native', 'prompt_tokens': 1, 'chat_template_kwargs': {}}):
            budget = case['payload']['max_tokens']
            response = {'model': 'arbitrary', 'choices': [{'text': '答案为42。',
                        'finish_reason': 'stop' if budget >= 129 else 'length'}]}
            self.assertEqual(validate_response(response, 'arbitrary')['finish_reason'], 'stop')

    def test_length_is_rejected_even_with_sse_done(self):
        from verification.protocol.cases import validate_response
        from verification.protocol.sse import parse
        with self.assertRaises(ValueError):
            validate_response({'model': 'x', 'choices': [{'text': '42', 'finish_reason': 'length'}]}, 'x')
        event = {'model': 'x', 'choices': [{'delta': {'content': '42'}, 'finish_reason': 'length'}]}
        with self.assertRaises(ValueError):
            parse([('data: ' + json.dumps(event) + '\n').encode(), b'\n', b'data: [DONE]\n', b'\n'], 'x')

    def test_template_payload_and_context_budget(self):
        from customer_workflow.compile import generation
        observed = {'prompt': '<user>question</user><assistant>', 'prompt_tokens': 450,
                    'chat_template_kwargs': {'enable_thinking': False}}
        rows = generation.cases('alias', observed)
        self.assertEqual(rows[0]['payload']['prompt'], observed['prompt'])
        self.assertFalse(rows[0]['payload']['add_special_tokens'])
        self.assertNotIn('chat_template_kwargs', rows[0]['payload'])
        self.assertEqual(rows[1]['payload']['chat_template_kwargs'], {'enable_thinking': False})
        self.assertEqual(rows[2]['payload']['chat_template_kwargs'], {'enable_thinking': False})
        self.assertTrue(all(row['payload']['max_tokens'] == 8192 for row in rows))
        self.assertEqual(generation.context_tokens([{'subset': {'max_prompt_tokens': 366}}], observed), 8642)
        self.assertEqual(generation.context_tokens([{'subset': {'max_prompt_tokens': 500}}], observed), 8692)
        observed['chat_template_kwargs'] = {}
        self.assertNotIn('chat_template_kwargs', generation.cases('other', observed)[1]['payload'])

    def test_native_rendering_ids_are_not_dict_keys_or_duplicated_special_tokens(self):
        from verification.customer import functional_inputs
        tokenizer = SimpleNamespace(get_chat_template=lambda: 'native-template',
            apply_chat_template=Mock(side_effect=['rendered', [1, 2, 3]]),
            encode=Mock(return_value=[1, 2, 3]))
        with patch.object(functional_inputs, 'template_switch', return_value=True):
            result = functional_inputs.render(tokenizer, [{'role': 'user', 'content': 'arbitrary'}])
        self.assertEqual(result['prompt_tokens'], 3)
        self.assertEqual(result['chat_template_kwargs'], {'enable_thinking': False})
        self.assertEqual(tokenizer.apply_chat_template.call_args.kwargs['return_dict'], False)
        tokenizer.encode.assert_called_once_with('rendered', add_special_tokens=False, truncation=False)
        tokenizer.apply_chat_template.side_effect = ['rendered', {'input_ids': [1, 2, 3]}]
        with patch.object(functional_inputs, 'template_switch', return_value=False), self.assertRaises(ValueError):
            functional_inputs.render(tokenizer, [{'role': 'user', 'content': 'arbitrary'}])

    def test_observation_identity_and_capacity_edges(self):
        from customer_workflow.compile import generation
        from customer_workflow.effects import functional_inputs
        from coordination.runtime import sources
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / 'tokenizer.json').write_text('{}')
            asset = {'path': str(root), 'files': [{'path': 'tokenizer.json',
                      'sha256': sources.ref(root / 'tokenizer.json')['sha256']}], 'weights': {'files': []}}
            task = {'model': {'model_name': 'alias'}}
            recipe = {'thinking_supported': False, 'model_max_context': 8195}
            request = generation.observation_request(task, asset, recipe)
            observed = {'inputs': request, 'prompt': 'native', 'prompt_tokens': 3, 'chat_template_kwargs': {}}
            self.assertEqual(generation.bind(observed, request, recipe), observed)
            observations = {'deployment': recipe, 'evaluator': {'python': sys.executable}}
            with patch('customer_workflow.effects.operation', return_value=observed):
                _, length = functional_inputs(None, task, asset, observations, [], root)
                self.assertEqual(length, 8195)
                recipe['model_max_context'] = 8194
                with self.assertRaisesRegex(ValueError, '上下文不足'):
                    functional_inputs(None, task, asset, observations, [], root)
            altered = copy.deepcopy(observed)
            altered['inputs']['model'] = 'wrong'
            with self.assertRaisesRegex(ValueError, '不属于'):
                generation.bind(altered, request, recipe)
            (root / 'tokenizer.json').write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError, '变化'):
                generation.bind(observed, request, recipe)

    def test_missing_or_inconsistent_template_fails(self):
        from verification.customer import functional_inputs
        tokenizer = SimpleNamespace(get_chat_template=lambda: '', apply_chat_template=Mock())
        with self.assertRaisesRegex(ValueError, '模板'):
            functional_inputs.render(tokenizer, [])
        tokenizer.get_chat_template = lambda: 'valid'
        tokenizer.apply_chat_template.side_effect = ['rendered', [1]]
        tokenizer.encode = Mock(return_value=[2])
        with patch.object(functional_inputs, 'template_switch', return_value=False), self.assertRaisesRegex(ValueError, '不一致'):
            functional_inputs.render(tokenizer, [])

    def test_template_override_is_not_silently_ignored(self):
        from customer_workflow.compile.generation import observation_request
        for key in ('tokenizer', 'chat_template', 'default_chat_template_kwargs'):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, '覆盖参数'):
                observation_request({}, {}, {'server_options': {key: 'custom'}})

    def test_actual_prompt_usage_must_equal_observed_input(self):
        from verification.protocol.usage import require_prompt
        require_prompt({'prompt_tokens': 3}, 0, 3)
        for actual in (None, {}, {'prompt_tokens': 2}, {'prompt_tokens': 4}):
            with self.subTest(actual=actual), self.assertRaisesRegex(ValueError, '不一致'):
                require_prompt(actual, 0, 3)

    def test_observation_includes_auxiliary_model_files_without_weakening_known_identity(self):
        from customer_workflow.compile.generation import observation_request
        from coordination.runtime.sources import ref
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            config = root / 'tokenizer_config.json'; config.write_text('{}')
            (root / 'chat_template.jinja').write_text('native template')
            asset = {'path': str(root), 'files': [{'path': config.name, 'sha256': ref(config)['sha256']}],
                     'weights': {'files': []}}
            request = observation_request({'model': {'model_name': 'arbitrary'}}, asset, {})
            self.assertEqual(set(request['files']), {'tokenizer_config.json', 'chat_template.jinja'})
            config.write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError, '身份'):
                observation_request({'model': {'model_name': 'arbitrary'}}, asset, {})

    def test_public_probe_plan_is_effect_free_and_protects_model_directory(self):
        import subprocess
        script = ROOT / 'skills/vllm-migration-execute/skills/verify-agent/scripts/customer.py'
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            model = root / 'model'; model.mkdir()
            request = root / 'request.json'
            request.write_text(json.dumps({'operation': 'functional_inputs', 'model_path': str(model)}))
            command = [sys.executable, '-B', str(script), '--request', str(request), '--run-id', 'plan', '--plan']
            result = subprocess.run([*command, '--work-root', str(root / 'evidence')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'not_run')
            self.assertFalse((root / 'evidence').exists())
            result = subprocess.run([*command, '--work-root', str(model / 'evidence')], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((model / 'evidence').exists())

    def test_new_template_file_invalidates_prior_observation(self):
        from verification.customer.functional_inputs import snapshot
        from coordination.runtime.sources import ref
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve();file = root / 'tokenizer.json';file.write_text('{}')
            request = {'model_path': str(root), 'files': {file.name: ref(file)['sha256']}, 'weight_files': []}
            self.assertEqual(snapshot(request), request['files'])
            (root / 'chat_template.jinja').write_text('new template')
            with self.assertRaisesRegex(ValueError, '文件集合'):
                snapshot(request)

    def test_native_template_without_thinking_switch_omits_parameter(self):
        from verification.customer import functional_inputs
        tokenizer = SimpleNamespace(get_chat_template=lambda: 'ordinary-template',
            apply_chat_template=Mock(side_effect=['rendered', [4, 5]]), encode=Mock(return_value=[4, 5]))
        with patch.object(functional_inputs, 'template_switch', return_value=False):
            result = functional_inputs.render(tokenizer, [{'role': 'user', 'content': 'arbitrary'}])
        self.assertEqual(result['chat_template_kwargs'], {})
        for call in tokenizer.apply_chat_template.call_args_list:
            self.assertNotIn('enable_thinking', call.kwargs)
