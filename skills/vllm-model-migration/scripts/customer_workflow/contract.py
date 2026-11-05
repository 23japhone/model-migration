"""客户字段与静态默认归一化；动态默认保留为待解析标记。"""
import copy
from pathlib import Path
import re

from .defaults.policy import CONTAINER, DATASETS, REPOSITORY, container_name, unspecified

FIELDS = {
    'model': ('model_repository_id', 'path', 'model_name'),
    'environment': ('image_name', 'container_name', 'available_npu_devices'),
    'accuracy': ('evaluator', 'datasets'),
}
FILES = ('benchmark_plan.json', 'accuracy_plan.json', 'deployment.json')


def default(value):
    return type(value) is int and value == -1


def fields(value, expected, location):
    if not isinstance(value, dict):
        raise ValueError(location + ' 必须是对象')
    if set(value) != set(expected):
        raise ValueError(location + ' 缺少字段 ' + ','.join(sorted(set(expected) - set(value))) +
                         '；未知字段 ' + ','.join(sorted(set(value) - set(expected))))


def optional_fields(value, expected, location):
    if not isinstance(value, dict):
        raise ValueError(location + ' 必须是对象')
    if value.keys() - set(expected):
        raise ValueError(location + ' 未知字段 ' + ','.join(sorted(value.keys() - set(expected))))
    return {key: -1 if unspecified(value.get(key)) else value[key] for key in expected}


def text(value, location):
    if not isinstance(value, str) or not value.strip() or value.strip() == '-1' or '\0' in value:
        raise ValueError(location + ' 必须为非空字符串')
    return value


def path(value, location):
    text(value, location)
    parsed = Path(value).expanduser()
    if not parsed.is_absolute() or '..' in parsed.parts:
        raise ValueError(location + ' 必须是绝对路径')
    return str(parsed)


def integer(value, location, minimum=1, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(location + ' 整数范围无效')
    return value


def sequence(value, location, check):
    if not isinstance(value, list) or not value:
        raise ValueError(location + ' 必须为非空列表')
    for item in value:
        check(item, location)
    if len(set(value)) != len(value):
        raise ValueError(location + ' 不允许重复项')
    return list(value)


def model(value):
    repo = value['model_repository_id']
    if not default(repo):
        text(repo, 'model.model_repository_id')
        if not re.fullmatch(REPOSITORY, repo):
            raise ValueError('model.model_repository_id 必须是 namespace/repository')
    if not default(value['path']):
        value['path'] = path(value['path'], 'model.path')
    if not default(value['model_name']):
        text(value['model_name'], 'model.model_name')


def environment(value):
    image = value['image_name']
    if not default(image):
        text(image, 'environment.image_name')
        if any(char.isspace() for char in image) or '://' in image:
            raise ValueError('environment.image_name 必须是镜像引用')
    name = value['container_name']
    if not default(name):
        text(name, 'environment.container_name')
        if not re.fullmatch(CONTAINER, name):
            raise ValueError('environment.container_name 非法，请明确填写合法容器名称')
    if not default(value['available_npu_devices']):
        value['available_npu_devices'] = sequence(value['available_npu_devices'], 'environment.available_npu_devices',
                                                 lambda item, field: integer(item, field, 0))


def dataset_fields(value):
    """校验条目明确字段；prompt 可以只补其中几项，不提前套用默认。"""
    value = optional_fields(value, ('name', 'path', 'subset_list', 'limit'), 'accuracy.datasets[]')
    name = text(value['name'], 'accuracy.datasets[].name')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
        raise ValueError('accuracy.datasets[].name 必须为原生评测数据集标识')
    if not default(value['path']):
        value['path'] = path(value['path'], 'accuracy.datasets[].path')
    if not default(value['subset_list']):
        value['subset_list'] = sequence(value['subset_list'], 'accuracy.datasets[].subset_list', text)
    if not default(value['limit']):
        integer(value['limit'], 'accuracy.datasets[].limit')
    return value


def dataset(value):
    value = dataset_fields(value)
    if default(value['path']) and value['name'] not in DATASETS:
        raise ValueError('accuracy.datasets[].path 必须为绝对路径；该数据集没有默认路径')
    return value


def dataset_list(items, check):
    if not isinstance(items, list) or not items:
        raise ValueError('accuracy.datasets 必须为非空列表')
    rows = [check(item) for item in items]
    if len({item['name'] for item in rows}) != len(rows):
        raise ValueError('accuracy.datasets 数据集名称不得重复')
    return rows


def accuracy(value):
    evaluator = value['evaluator']
    if not default(evaluator):
        evaluator = text(evaluator, 'accuracy.evaluator').lower()
    if not default(evaluator) and evaluator not in ('evalscope', 'aisbench'):
        raise ValueError('accuracy.evaluator 仅支持 evalscope 或 aisbench')
    value['evaluator'] = evaluator
    items = value['datasets']
    if default(items):
        return
    value['datasets'] = dataset_list(items, dataset)


def validate(raw):
    """只校验显式值；未指定字段保留内部 -1，不生成任何默认值。"""
    fields(raw, FIELDS, 'task')
    value = copy.deepcopy(raw)
    for group, keys in FIELDS.items():
        value[group] = optional_fields(value[group], keys, group)
    model(value['model'])
    environment(value['environment'])
    accuracy(value['accuracy'])
    return value


def normalize(raw):
    """在合法声明上应用静态默认；也用于保留原 inputs 快照语义。"""
    value = validate(raw)
    if default(value['environment']['container_name']):
        value['environment']['container_name'] = container_name(value['model']['model_name'])
    accuracy = value['accuracy']
    if default(accuracy['evaluator']):
        accuracy['evaluator'] = 'evalscope'
    if default(accuracy['datasets']):
        accuracy['datasets'] = [dataset({'name': name}) for name in DATASETS]
    for row in accuracy['datasets']:
        if default(row['path']):
            row['path'] = '/data/' + row['name']
        if default(row['limit']):
            row['limit'] = None
    return value
