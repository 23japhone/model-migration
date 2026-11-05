"""原生 catalog 的容器协议、真实 token 计数及预算消费回归。"""
from collections import UserDict
import copy
import dataclasses
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(ROOT / 'skills/vllm-precision-align/skills/verify-agent-plus/scripts'))
sys.path.insert(0, str(ROOT / 'scripts'))


class KeyedSubsets:
    def __init__(self, groups):
        self.groups = groups

    def keys(self):
        return self.groups.keys()

    def __getitem__(self, key):
        return self.groups[key]


@dataclasses.dataclass
class BenchmarkMeta:
    name: str = 'arbitrary-benchmark'


class Adapter:
    benchmark_meta = BenchmarkMeta()
    eval_split = 'test'

    def __init__(self, subsets, groups):
        self.subset_list = subsets
        self.groups = groups

    def load_dataset(self):
        return self.groups


class ChatTokenizer:
    """模拟公开 return_dict 参数；省略时允许旧/新两种默认行为。"""
    def __init__(self, default_dict=True, lengths=None):
        self.default_dict = default_dict
        self.lengths = lengths or {}
        self.calls = []

    def apply_chat_template(self, messages, **kwargs):
        self.calls.append((copy.deepcopy(messages), dict(kwargs)))
        text = ''.join(message['content'] for message in messages)
        ids = list(range(self.lengths.get(text, len(text))))
        if kwargs.get('return_dict', self.default_dict):
            return UserDict({'input_ids': ids, 'attention_mask': [1] * len(ids)})
        return ids


class CatalogProtocolTests(unittest.TestCase):
    def inspect(self, wrapper, limit=2, malformed=None, tokenizer=None):
        from precision.runtime.evalscope import catalog
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            data = root / 'data'
            data.mkdir()
            raw = b'original data bytes\n'
            (data / 'original.txt').write_bytes(raw)
            subsets = ['arbitrary-a', 'arbitrary-b']
            request = {'model': 'alias', 'model_path': str(root / 'model'),
                       'dataset': {'name': 'arbitrary-benchmark', 'path': str(data),
                                   'subset_list': subsets, 'limit': limit}}
            visited = []
            sample = lambda text: SimpleNamespace(input=text, tools=None, sandbox=None, files=None, setup=None)
            def benchmark(name, config):
                selected = config['dataset_args'][name]['subset_list']
                count = 3 if config['limit'] is None else min(config['limit'], 3)
                groups = {key: [sample(key + str(i)) for i in range(count)] for key in selected}
                return Adapter(selected, malformed if malformed is not None else wrapper(groups))
            def tokenize(messages, **kwargs):
                visited.append(messages[0]['content'])
                if tokenizer is not None:
                    return tokenizer.apply_chat_template(messages, **kwargs)
                return [1] * len(messages[0]['content'])
            registry = ModuleType('evalscope.api.registry')
            registry.get_benchmark = benchmark
            transformers = ModuleType('transformers')
            stub_tokenizer = SimpleNamespace(apply_chat_template=tokenize)
            transformers.AutoTokenizer = SimpleNamespace(from_pretrained=Mock(return_value=stub_tokenizer))
            with patch.dict(sys.modules, {'evalscope.api.registry': registry, 'transformers': transformers}), \
                    patch.object(catalog, 'native_config', side_effect=lambda raw: raw), \
                    patch.object(catalog, 'capability'), patch.object(catalog, 'template_switch', return_value=False), \
                    patch.object(catalog.importlib.metadata, 'version', return_value='fixture-version'):
                result = catalog.inspect(request, root / 'attempt')
            self.assertEqual((data / 'original.txt').read_bytes(), raw)
            return result, visited

    def test_mapping_protocol_preserves_counts_order_tokens_and_data_identity(self):
        for limit, count in ((2, 2), (20, 3), (-1, 3)):
            with self.subTest(limit=limit):
                plain, plain_order = self.inspect(dict, limit)
                native, native_order = self.inspect(KeyedSubsets, limit)
                self.assertEqual(plain['subsets'], native['subsets'])
                self.assertEqual(plain_order, native_order)
                self.assertEqual(native_order, [key + str(i) for key in ('arbitrary-a', 'arbitrary-b') for i in range(count)])
                self.assertEqual([row['count'] for row in native['subsets']], [count, count])
                self.assertEqual([row['max_prompt_tokens'] for row in native['subsets']], [len('arbitrary-a0')] * 2)
                self.assertEqual(plain['data_files'], native['data_files'])

    def test_wrong_extra_and_empty_subsets_remain_rejected(self):
        for groups in ({'wrong': [1]}, {'arbitrary-a': [1], 'extra': [1]}, {'arbitrary-a': []}):
            with self.subTest(groups=groups), self.assertRaisesRegex(ValueError, '唯一|子集'):
                self.inspect(KeyedSubsets, malformed=KeyedSubsets(groups))

    def test_missing_public_protocol_reports_actual_type(self):
        for value in ([], SimpleNamespace(), SimpleNamespace(keys=lambda: ['arbitrary-a'])):
            with self.subTest(value=type(value).__name__), self.assertRaisesRegex(ValueError, type(value).__name__):
                self.inspect(KeyedSubsets, malformed=value)


    def test_catalog_maximum_drives_context_budget_and_capacity_guard(self):
        from customer_workflow.compile.accuracy import context_tokens
        from customer_workflow.runner import evaluation_request
        lengths = {'arbitrary-a0': 3, 'arbitrary-a1': 17, 'arbitrary-a2': 5,
                   'arbitrary-b0': 2, 'arbitrary-b1': 11, 'arbitrary-b2': 7}
        for default_dict in (False, True):
            with self.subTest(default_dict=default_dict):
                tokenizer = ChatTokenizer(default_dict, lengths)
                result, order = self.inspect(KeyedSubsets, limit=3, tokenizer=tokenizer)
                self.assertEqual(order, list(lengths))
                self.assertEqual([row['count'] for row in result['subsets']], [3, 3])
                self.assertEqual([row['max_prompt_tokens'] for row in result['subsets']], [17, 11])
                jobs = [{'subset': row, 'prepared': {}} for row in result['subsets']]
                self.assertEqual(context_tokens(jobs), 8209)
                session = SimpleNamespace(value={'resolved_deployment': {'capacity': {'max_model_len': 8200}}})
                observations = {'evaluator': {}, 'deployment': {'thinking_supported': False}}
                with patch('customer_workflow.runner.accuracy.request', return_value='compiled') as compile_request:
                    with self.assertRaisesRegex(ValueError, '8192'):
                        evaluation_request(session, {}, jobs[0], observations, 'http://127.0.0.1:8000')
                    compile_request.assert_not_called()
                    session.value['resolved_deployment']['capacity']['max_model_len'] = 8209
                    self.assertEqual(evaluation_request(session, {}, jobs[0], observations,
                                                        'http://127.0.0.1:8000'), 'compiled')


class TokenLengthTests(unittest.TestCase):
    def sample(self, value):
        return SimpleNamespace(input=value, tools=None, sandbox=None, files=None, setup=None)

    def test_list_and_mapping_defaults_count_tokens_including_length_two(self):
        from precision.runtime.evalscope.catalog import tokens
        for default_dict in (False, True):
            for length in (3, 17, 5, 2):
                with self.subTest(default_dict=default_dict, length=length):
                    tokenizer = ChatTokenizer(default_dict)
                    self.assertEqual(tokens(self.sample('a' * length), tokenizer, False), length)

    def test_messages_and_template_options_keep_the_single_sample_contract(self):
        from precision.runtime.evalscope.catalog import tokens
        messages = [SimpleNamespace(role='system', content='规则'),
                    SimpleNamespace(role='user', content='问题'),
                    SimpleNamespace(role='assistant', content='回答'),
                    SimpleNamespace(role='user', content='追问')]
        for value in ('中文 text', messages):
            for thinking in (False, True):
                with self.subTest(value=value, thinking=thinking):
                    sample = self.sample(value)
                    before = copy.deepcopy(sample)
                    tokenizer = ChatTokenizer(False)
                    tokens(sample, tokenizer, thinking)
                    expected_messages = [{'role': 'user', 'content': value}] if isinstance(value, str) else [
                        {'role': m.role, 'content': m.content} for m in value]
                    expected = {'tokenize': True, 'add_generation_prompt': True, 'return_dict': False,
                                'return_tensors': None, 'padding': False, 'truncation': False}
                    if thinking:
                        expected['enable_thinking'] = False
                    self.assertEqual(tokenizer.calls, [(expected_messages, expected)])
                    self.assertEqual(sample, before)

    def test_unexpected_output_shapes_fail_with_the_actual_type(self):
        from precision.runtime.evalscope.catalog import tokens
        for value in (None, 7, 'text', [], {}, UserDict({'input_ids': [1, 2]}), [[1, 2]], [1, '2'], [True]):
            with self.subTest(value=value):
                tokenizer = SimpleNamespace(apply_chat_template=Mock(return_value=value))
                with self.assertRaisesRegex(ValueError, type(value).__name__):
                    tokens(self.sample('unchanged input'), tokenizer, False)
                tokenizer.apply_chat_template.assert_called_once()

    def test_long_samples_are_not_truncated_or_reinterpreted_as_a_batch(self):
        from precision.runtime.evalscope.catalog import tokens
        tokenizer = ChatTokenizer(True, {'long input': 9001})
        self.assertEqual(tokens(self.sample('long input'), tokenizer, False), 9001)
        self.assertIs(tokenizer.calls[0][1]['truncation'], False)


if __name__ == '__main__':
    unittest.main()
