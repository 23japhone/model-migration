"""规范化预检请求并保护模型、源码和安装资源。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[6] / "scripts"))
from skill_paths import plugin_root as release_root, skill_root as release_skill, owned_files, registration_bundle
from pathlib import Path
import re

from .evidence import read_json


TOP_FIELDS = {'model', 'target', 'sources', 'reviews', 'features', 'implementation',
              'adaptation', 'prepare_handoff'}
MODEL_FIELDS = {'path', 'task', 'modalities', 'dtype', 'quantization'}


def object_fields(value, allowed, required, label):
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise ValueError(label + '字段缺失、未知或不是对象')
    return dict(value)


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise ValueError(label + '必须为非空字符串')
    return value


def string_list(value, label, allowed=None):
    if not isinstance(value, list) or not value:
        raise ValueError(label + '必须为非空列表')
    items = [nonempty(item, label) for item in value]
    if len(items) != len(set(items)) or (allowed is not None and set(items) - allowed):
        raise ValueError(label + '存在重复或不支持的值')
    return items


def absolute(value, base):
    path = Path(nonempty(value, '路径')).expanduser()
    return str((base / path if not path.is_absolute() else path).resolve())


def normalize_model(value, work_root):
    model = object_fields(value, MODEL_FIELDS, MODEL_FIELDS, 'model')
    model['path'] = absolute(model['path'], work_root)
    if model['task'] not in ('chat', 'generate', 'embedding', 'rerank'):
        raise ValueError('model.task 不支持')
    model['modalities'] = string_list(model['modalities'], 'modalities', {'text', 'image', 'audio', 'video'})
    if model['dtype'] not in ('bfloat16', 'float16', 'float32'):
        raise ValueError('model.dtype 必须明确为 bfloat16、float16 或 float32')
    if model['quantization'] is not None:
        nonempty(model['quantization'], 'quantization')
    return model


def normalize_target(value):
    fields = {'vllm', 'vllm_ascend', 'platform', 'hardware'}
    target = object_fields(value, fields | {'backend'}, fields, 'target')
    if target.get('backend', 'vllm-ascend') != 'vllm-ascend':
        raise ValueError('target.backend 必须为 vllm-ascend')
    for name in fields - {'hardware'}:
        nonempty(target[name], 'target.' + name)
    hardware = object_fields(target['hardware'], {'name', 'family'}, {'name', 'family'}, 'hardware')
    for value in hardware.values():
        nonempty(value, 'hardware')
    target['hardware'] = hardware
    return target


def normalize_sources(value, base):
    from .source_identity import validate
    value = object_fields(value, {'vllm', 'vllm_ascend'}, {'vllm', 'vllm_ascend'}, 'sources')
    output = {}
    for name, item in value.items():
        item = object_fields(item, {'path', 'revision', 'expected_identity', 'kind', 'distribution', 'version', 'image_id'}, {'path'}, name)
        validate(item)
        item['path'] = absolute(item['path'], base)
        if 'expected_identity' in item and not re.fullmatch('[0-9a-f]{64}', str(item['expected_identity'])):
            raise ValueError('expected_identity 必须为 SHA256')
        output[name] = item
    return output


def normalize_implementation(value, base):
    fields = {'architecture', 'path', 'class_name'}
    value = object_fields(value, fields, fields, 'implementation')
    nonempty(value['architecture'], 'architecture')
    if not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', str(value['class_name'])):
        raise ValueError('class_name 无效')
    value['path'] = absolute(value['path'], base)
    return value


def normalize_adaptation(value, base):
    value = object_fields(value, {'allowed_scope', 'reference'}, {'allowed_scope', 'reference'}, 'adaptation')
    value['allowed_scope'] = string_list(value['allowed_scope'], 'allowed_scope', {'model', 'processor', 'platform'})
    reference = object_fields(value['reference'], {'path', 'class_name', 'sha256'},
                              {'path', 'class_name', 'sha256'}, 'reference')
    reference['path'] = absolute(reference['path'], base)
    nonempty(reference['class_name'], 'reference.class_name')
    if not re.fullmatch('[0-9a-f]{64}', str(reference['sha256'])):
        raise ValueError('reference.sha256 无效')
    value['reference'] = reference
    return value


def load_request(path, work_root):
    validate_resources()
    base = Path(path).expanduser().resolve().parent
    value, _ = read_json(path)
    request = object_fields(value, TOP_FIELDS, {'model', 'target', 'sources', 'reviews'}, '请求')
    request['model'] = normalize_model(request['model'], work_root)
    request['target'] = normalize_target(request['target'])
    request['sources'] = normalize_sources(request['sources'], base)
    if not isinstance(request['reviews'], list):
        raise ValueError('reviews 必须为列表')
    request['reviews'] = normalize_reviews(request['reviews'], base)
    request['features'] = string_list(request.get('features', ['eager']), 'features')
    if 'implementation' in request:
        request['implementation'] = normalize_implementation(request['implementation'], base)
    if 'adaptation' in request:
        request['adaptation'] = normalize_adaptation(request['adaptation'], base)
    if 'prepare_handoff' in request:
        request['prepare_handoff'] = absolute(request['prepare_handoff'], base)
    return request


def validate_resources():
    owner = release_skill('vllm-model-migration')
    try:
        schema, _ = read_json(owner / 'references/task-result.schema.json')
    except (OSError, ValueError) as error:
        raise ValueError('缺少或损坏共同 Schema，请完整复制静态资源') from error
    required = {'schema_version', 'run_id', 'task_id', 'inputs', 'status', 'conclusions', 'evidence', 'uncovered'}
    properties = schema.get('properties')
    if not isinstance(properties, dict) or not isinstance(properties.get('schema_version'), dict):
        raise ValueError('共同 Schema properties 结构无效')
    if properties['schema_version'].get('const') != '1.0':
        raise ValueError('共同 Schema 版本不兼容')
    fields = schema.get('required')
    if not isinstance(fields, list) or any(not isinstance(field, str) for field in fields):
        raise ValueError('共同 Schema required 结构无效')
    if not required.issubset(set(fields)):
        raise ValueError('共同 Schema 外层契约不完整')


def normalize_reviews(records, base):
    normalized = []
    for record in records:
        if isinstance(record, dict):
            record = dict(record)
            if isinstance(record.get('evidence_file'), str) and record['evidence_file']:
                record['evidence_file'] = absolute(record['evidence_file'], base)
        normalized.append(record)
    return normalized


def output_target(request, work_root, run_id):
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,127}', run_id) or run_id in ('.', '..'):
        raise ValueError('run_id 必须为安全的单层标识')
    root = Path(work_root).expanduser().resolve()
    target = root / run_id / 'vllm-model-precheck'
    protected = release_root()
    inputs = [Path(request['model']['path']), protected]
    inputs.extend(Path(source['path']) for source in request['sources'].values())
    for path in inputs:
        if root.is_relative_to(path) or target.is_relative_to(path) or path.is_relative_to(target):
            raise ValueError('运行证据必须位于模型、源码和技能安装资源之外')
    if target.resolve() != target:
        raise ValueError('运行目录不能通过符号链接重定向')
    return target
