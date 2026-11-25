"""可跨设备比较的实际模板、分词资源、请求与服务生成设置。"""
import hashlib
import json
from pathlib import Path

from ..inputs import canonical, deployment


def tokenizer_files(files):
    names = ('tokenizer', 'vocab', 'merges', 'added_tokens', 'special_tokens', 'sentencepiece', 'spiece')
    return {p: digest for p, digest in files.items()
            if Path(p).name.startswith(names) or Path(p).suffix == '.tiktoken'}


def defaults(flags, files):
    options = {key: value for key, value in flags.items() if key not in ('--chat-template', '--tokenizer')}
    options.setdefault('--generation-config', 'auto')
    for key in ('--override-generation-config', '--default-chat-template-kwargs', '--logits-processors'):
        if isinstance(options.get(key), str):
            try:
                options[key] = json.loads(options[key])
            except ValueError:
                pass  # 例如以完整类名表示的原生 logits processor。
    generation_files = {p: digest for p,digest in files.items() if Path(p).name.startswith('generation_config')}
    return {'overrides': options, 'model_generation_files': generation_files}


def tokenization(template, model_files):
    tokens = tokenizer_files(model_files)
    return {'chat_template_sha256': hashlib.sha256(template.encode()).hexdigest() if template else None,
            'tokenizer_sha256': canonical(tokens) if tokens else None}


def reader_digests(rows):
    return {row['subset']: row['reader_samples_sha256'] for row in rows if row.get('reader_samples_sha256')}


def service_conditions(flags, model_files, template_sha, tokenizer_sha):
    # --config 仅解出了 YAML 文件身份，文件内可能引用另一份模板、tokenizer 或生成配置。
    if '--config' in flags:
        return {'chat_template_sha256': None, 'tokenizer_sha256': None, 'server_generation': None}
    return {'chat_template_sha256': template_sha, 'tokenizer_sha256': tokenizer_sha,
            'server_generation': defaults(flags, model_files) if model_files else None}


def recorded(index, before):
    budget = index.get('prompt_budget', {})
    model_files = index.get('model_files', {})
    static = service_conditions(before['generation'], model_files, budget.get('chat_template_sha256'),
                                budget.get('tokenizer_sha256'))
    return {**static, 'reader_sha256_by_subset': reader_digests(index.get('benchmark_conditions', []))}


def ordinary(conditions):
    files = conditions['model_files']
    flags = conditions['server_generation']
    # 旧测量仅能证明显式模板，默认模板没有冻结实际渲染文本。
    override = flags.get('--chat-template')
    if isinstance(override, dict):
        template_sha = override.get('sha256')
    elif isinstance(override, str):
        template_sha = hashlib.sha256(override.encode()).hexdigest()
    else:
        template_sha = None
    tokens = tokenizer_files(files)
    static = service_conditions(flags, files, template_sha, canonical(tokens) if tokens else None)
    return {**static, 'reader_sha256_by_subset': reader_digests(conditions['datasets'])}


def check_deployment(original_ref, current_ref, flags):
    from ..runtime.service import generation_parameters
    if original_ref == current_ref:
        return
    # 不同回执或端点可通过相同的服务生成配置证明复用条件。
    _, previous, first = deployment(original_ref)
    _, current, second = deployment(current_ref)
    if first == second:
        return
    for report in (previous, current):
        if not isinstance(report['inputs']['service'].get('command'),list):
            raise ValueError('变更的服务交接缺少可核对的实际生成配置')
    left, right = (generation_parameters(r['inputs']['service']['command']) for r in (previous, current))
    if left != right or flags != right:
        raise ValueError('变更的服务交接与历史实际模板、分词或生成默认配置不符')
