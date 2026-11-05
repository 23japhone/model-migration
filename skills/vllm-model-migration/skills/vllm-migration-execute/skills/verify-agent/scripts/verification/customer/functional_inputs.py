"""只读生成模型原生功能输入；不加载权重、启动服务或执行推理。"""
import hashlib
import importlib.metadata
from pathlib import Path

from ..evidence import sha256
from model_cache import checked


def template_switch(tokenizer):
    from jinja2 import Environment, meta
    template = tokenizer.get_chat_template()
    return 'enable_thinking' in meta.find_undeclared_variables(Environment().parse(template))


def render(tokenizer, messages):
    template = tokenizer.get_chat_template()
    if not isinstance(template, str) or not template.strip():
        raise ValueError('模型缺少有效聊天模板')
    kwargs = {'enable_thinking': False} if template_switch(tokenizer) else {}
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, **kwargs)
    ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                        return_dict=False, return_tensors=None, padding=False,
                                        truncation=False, **kwargs)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('原生模板没有生成非空文本')
    if not isinstance(ids, list) or not ids or any(type(n) is not int or n < 0 for n in ids):
        raise ValueError('功能输入 tokenizer 须返回非空整数 ID 列表')
    encoded = tokenizer.encode(prompt, add_special_tokens=False, truncation=False)
    if encoded != ids:
        raise ValueError('文本接口与聊天模板的 token ID 不一致')
    return {'prompt': prompt, 'prompt_tokens': len(ids), 'chat_template_kwargs': kwargs,
            'template_sha256': hashlib.sha256(template.encode()).hexdigest()}


def snapshot(request):
    root = Path(request['model_path'])
    files = request['files']
    if not root.is_absolute() or not isinstance(files, dict) or not files:
        raise ValueError('功能输入观察缺少模型目录或非权重文件身份')
    names = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if names - set(request['weight_files']) != set(files):
        raise ValueError('模型/tokenizer 文件集合与观察请求不一致')
    actual = {name: sha256(checked(root, name)) for name in files}
    if actual != files:
        raise ValueError('功能输入观察的模型/tokenizer 文件身份已变化')
    return actual


def inspect(request, attempt):
    try:
        from transformers import AutoTokenizer
        from jinja2 import TemplateError
    except ImportError as error:
        raise ValueError('功能输入观察需要已有 Transformers 环境，不自动安装') from error
    before = snapshot(request)
    tokenizer = AutoTokenizer.from_pretrained(request['model_path'], local_files_only=True, trust_remote_code=False)
    try:
        result = render(tokenizer, request['messages'])
    except TemplateError as error:
        raise ValueError('原生聊天模板渲染失败：' + str(error)) from error
    if snapshot(request) != before:
        raise ValueError('模型/tokenizer 在观察期间变化')
    return {**result, 'inputs': request, 'transformers_version': importlib.metadata.version('transformers')}
