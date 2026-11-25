"""正式推理前，用实际模板、请求参数和本地 tokenizer 计算每道题的上下文预算。"""
import json
from pathlib import Path

from ..evidence import load, sha256
from .runtime import tokenization


def template_text(value, model):
    if isinstance(value, str):
        return value
    if not isinstance(value, dict) or not value.get('sha256'):
        raise ValueError('无法固定服务端实际 chat_template')
    matches = sorted(k for k, digest in model['files'].items() if digest == value['sha256'])
    if not matches:
        raise ValueError('服务自定义的 chat_template 未包含在已绑定的模型文件中')
    return (Path(model['root'])/matches[0]).read_text(encoding='utf-8')


def tokenizer_settings(raw, payload):
    from transformers import AutoTokenizer
    model = payload['handoff']['model']
    server = payload['service_snapshot']['generation']
    tokenizer = AutoTokenizer.from_pretrained(model['root'], local_files_only=True, trust_remote_code=False)
    if '--tokenizer' in server:
        requested = server['--tokenizer']
        if not isinstance(requested, dict) or not requested.get('files'):
            raise ValueError('服务 tokenizer 未绑定到模型文件')
        if any(model['files'].get(k) != digest for k, digest in requested['files'].items()):
            raise ValueError('实际服务指定的 tokenizer 不同')
    kwargs = json.loads(server.get('--default-chat-template-kwargs', '{}'))
    extra = raw['generation_config'].get('extra_body') or {}
    kwargs.update(extra.get('chat_template_kwargs', {}))
    template = extra.get('chat_template')
    if template is None and '--chat-template' in server:
        template = template_text(server['--chat-template'], model)
    if raw.get('chat_template') and tokenizer.get_chat_template(template) != raw['chat_template']:
        raise ValueError('原生 API 的实际请求或服务模板与指定 chat_template 不同')
    return tokenizer, template, kwargs


def prompt_length(sample, tokenizer, template, kwargs):
    messages = sample['input']
    if not isinstance(messages, list):
        raise ValueError('原生 Chat 请求须提供冻结的 role/content 消息')
    values = [{'role': msg['role'], 'content': msg['content']} for msg in messages]
    ids = tokenizer.apply_chat_template(values, chat_template=template, tokenize=True, add_generation_prompt=True,
                                        return_dict=False, return_tensors=None, padding=False, truncation=False, **kwargs)
    if not isinstance(ids, list) or not ids or any(type(token) is not int for token in ids):
        raise ValueError('实际 tokenizer 未产出单题 token ID')
    return len(ids)


def inspect(raw, tasks, payload):
    tokenizer, template, kwargs = tokenizer_settings(raw, payload)
    maximum = payload['service_snapshot']['model']['max_model_len']
    limit = raw['generation_config'].get('max_tokens')
    result = {}
    for task in tasks:
        snap = task['sample_snapshot']
        if sha256(snap['path']) != snap['sha256']:
            raise ValueError('原生样本快照在预算核对前发生变化')
        lengths = [prompt_length(sample, tokenizer, template, kwargs) for sample in load(snap['path'])]
        minimum_output = limit if limit is not None else 1
        if not lengths or max(lengths) + minimum_output > maximum:
            raise ValueError('实际 prompt token 数与输出预算之和超过模型上下文：' + task['subset'])
        result[task['subset']] = {'count': len(lengths), 'minimum': min(lengths), 'maximum': max(lengths),
                                   'minimum_remaining_context': maximum - max(lengths)}
    identity = tokenization(tokenizer.get_chat_template(template), payload['handoff']['model']['files'])
    return {'prompt_tokens': result, 'max_tokens': limit, 'max_model_len': maximum,
            'output_budget_source': 'explicit' if limit is not None else 'native_service_default',
            'service_template_verified': '--config' not in payload['service_snapshot']['generation'], **identity}
