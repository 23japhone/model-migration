"""客户功能输入观察绑定、生成预算及三种功能请求。"""
import copy
from pathlib import Path

from coordination.runtime import sources
from model_cache import checked

OUTPUT_TOKENS = 8192
QUESTION = '请计算 17 + 25，并简短回答。'


def model_files(asset):
    root = Path(asset['path'])
    weights = set(asset['weights']['files'])
    actual = {p.relative_to(root).as_posix(): sources.ref(checked(root, p.relative_to(root)))['sha256']
              for p in root.rglob('*') if p.is_file() and p.relative_to(root).as_posix() not in weights}
    expected = {row['path']: row['sha256'] for row in asset['files'] if row['path'] not in weights}
    if any(actual.get(name) != digest for name, digest in expected.items()):
        raise ValueError('模型/tokenizer 已知文件身份与准备回执不一致')
    return actual


def observation_request(task, asset, recipe):
    overrides = {'tokenizer', 'tokenizer_revision', 'tokenizer_mode', 'chat_template', 'chat_template_content_format',
                 'default_chat_template_kwargs', 'hf_overrides', 'trust_remote_code'}
    if overrides & recipe.get('server_options', {}).keys():
        raise ValueError('功能输入观察需绑定服务实际 tokenizer/模板；当前只支持模型原生模板，不能忽略启动覆盖参数')
    weights = set(asset['weights']['files'])
    files = model_files(asset)
    return {'operation': 'functional_inputs', 'model': task['model']['model_name'], 'model_path': asset['path'],
            'files': files, 'weight_files': sorted(weights), 'messages': [{'role': 'user', 'content': QUESTION}]}


def bind(observed, request, recipe):
    if observed.get('inputs') != request:
        raise ValueError('功能输入观察不属于本次模型、文件或问题')
    names = {p.relative_to(request['model_path']).as_posix() for p in Path(request['model_path']).rglob('*') if p.is_file()}
    if names - set(request['weight_files']) != set(request['files']):
        raise ValueError('功能输入观察的模型/tokenizer 文件集合已变化')
    for name, digest in request['files'].items():
        if sources.ref(checked(Path(request['model_path']), name))['sha256'] != digest:
            raise ValueError('功能输入观察的模型/tokenizer 文件已变化')
    kwargs = observed.get('chat_template_kwargs')
    if kwargs not in ({}, {'enable_thinking': False}):
        raise ValueError('功能输入模板选项无效')
    if type(recipe.get('thinking_supported')) is not bool or bool(kwargs) != recipe['thinking_supported']:
        raise ValueError('功能输入模板的 thinking 能力与部署观察不一致')
    if not isinstance(observed.get('prompt'), str) or not observed['prompt'].strip():
        raise ValueError('功能输入缺少原生渲染文本')
    count = observed.get('prompt_tokens')
    if type(count) is not int or count < 1:
        raise ValueError('功能输入缺少真实正整数 token 长度')
    return observed


def context_tokens(jobs, functional):
    lengths = [functional['prompt_tokens'], *[job['subset']['max_prompt_tokens'] for job in jobs]]
    if any(type(n) is not int or n < 1 for n in lengths):
        raise ValueError('上下文预算缺少真实正整数输入长度')
    return max(lengths) + OUTPUT_TOKENS


def deployment_context(jobs, functional, recipe):
    complete_jobs = [job for job in jobs if 'blocked_reason' not in job]
    required = context_tokens(complete_jobs, functional)
    length = (recipe['model_max_context'] if len(complete_jobs) != len(jobs) or not jobs else required)
    if required > length or length > recipe['model_max_context']:
        raise ValueError('模型上下文不足以容纳功能/精度输入及完整 8192 输出预算')
    return length


def cases(model, functional):
    rows = []
    for kind in ('text', 'chat', 'stream'):
        payload = {'model': model, 'temperature': 0, 'max_tokens': OUTPUT_TOKENS, 'stream': kind == 'stream'}
        if kind == 'text':
            payload.update(prompt=functional['prompt'], add_special_tokens=False)
        else:
            payload['messages'] = [{'role': 'user', 'content': QUESTION}]
            if functional['chat_template_kwargs']:
                payload['chat_template_kwargs'] = copy.deepcopy(functional['chat_template_kwargs'])
        rows.append({'id': kind, 'kind': kind, 'route': '/v1/completions' if kind == 'text' else '/v1/chat/completions',
                     'required': True, 'payload': payload, 'semantic': {'mode': 'review'},
                     'expected_prompt_tokens': functional['prompt_tokens']})
    # usage须存在以复核实际服务分词；SSE默认不返回usage，显式请求。
    rows[2]['payload']['stream_options'] = {'include_usage': True}
    return rows
