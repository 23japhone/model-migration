"""不读取资产或主机的客户默认策略。"""
import hashlib
import re

DATASETS = ('ceval', 'gsm8k')
CONTAINER = r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}'
REPOSITORY = r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*'


def unspecified(value):
    return value is None or type(value) is int and value == -1


def container_name(model_name):
    if unspecified(model_name):
        return -1
    name = 'vllm_' + model_name
    if re.fullmatch(CONTAINER, name):
        return name
    slug = re.sub(r'[^A-Za-z0-9_.-]+', '-', model_name).strip('-') or 'model'
    suffix = hashlib.sha256(model_name.encode('utf-8')).hexdigest()[:12]
    return 'vllm_' + slug[:83] + '-' + suffix


def pending(task):
    return [group + '.' + key for group, keys in (
        ('model', ('model_name',)), ('environment', ('container_name', 'available_npu_devices')))
        for key in keys if unspecified(task[group][key])]
