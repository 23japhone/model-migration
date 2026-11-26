"""Agent 对客户意图的结构化结论；脚本不做关键词式自然语言分类。"""
import copy

from coordination.request import fields
from .contract import FIELDS, dataset_fields, dataset_list, validate as validate_task
from .defaults.policy import unspecified


def validate(value):
    fields(value, ('deployment', 'accuracy', 'performance', 'conflicts'))
    for key in ('deployment', 'accuracy', 'performance'):
        if type(value[key]) is not bool:
            raise ValueError('意图结论须是布尔值')
    if not isinstance(value['conflicts'], list) or any(not isinstance(x, str) or not x for x in value['conflicts']):
        raise ValueError('冲突须为待确认事项列表')
    if not value['deployment'] or not (value['accuracy'] or value['performance']):
        raise ValueError('此客户入口用于完整部署与基线交付；独立任务使用既有入口')
    return value


def parameters(interpreted):
    """复用客户显式值规则；非法 prompt 不是需要用户取舍的业务冲突。"""
    if not isinstance(interpreted, dict):
        raise ValueError('prompt_fields 必须为对象')
    value = {group: {} for group in FIELDS}
    for field, expected in interpreted.items():
        if not isinstance(field, str):
            raise ValueError('意图显式参数使用未知字段')
        parts = field.split('.')
        if len(parts) != 2 or parts[0] not in FIELDS or parts[1] not in FIELDS[parts[0]]:
            raise ValueError('意图显式参数使用未知字段')
        if field == 'accuracy.datasets':
            if not unspecified(expected):
                dataset_list(expected, dataset_fields)
            continue
        value[parts[0]][parts[1]] = expected
    return validate_task(value)


def dataset_values(actual, expected):
    """名称序列固定后补齐叶子；子集列表本身不合并、不重排。"""
    if unspecified(expected):
        return copy.deepcopy(actual), []
    if unspecified(actual):
        return copy.deepcopy(expected), []
    if [row['name'] for row in actual] != [row['name'] for row in expected]:
        return copy.deepcopy(actual), ['accuracy.datasets (数据集名称、数量或顺序)']
    rows, differences = copy.deepcopy(actual), []
    for index, (row, selected) in enumerate(zip(rows, expected)):
        for key in ('path', 'subset_list', 'limit'):
            requested = selected.get(key)
            if unspecified(requested):
                continue
            current = row.get(key)
            if unspecified(current):
                row[key] = copy.deepcopy(requested)
            elif type(current) is not type(requested) or current != requested:
                differences.append(f"accuracy.datasets[{index}].{key} ({row['name']})")
    return rows, differences


def compose(task, interpreted):
    """一次返回实际声明及冲突；本地权重身份另由模型复核门禁检查。"""
    checked = parameters(interpreted)
    declared = validate_task({**dict.fromkeys(FIELDS, {}), **task})
    value, result = copy.deepcopy(task), []
    for field, expected in interpreted.items():
        group, key = field.split('.')
        actual = declared[group][key]
        if field == 'accuracy.datasets':
            value[group][key], differences = dataset_values(value[group].get(key), expected)
            result.extend(differences)
        elif unspecified(actual):
            value[group][key] = checked[group][key]
        elif not unspecified(checked[group][key]) and actual != checked[group][key]:
            result.append(field)
    return value, result


def conflicts(task, interpreted):
    return compose(task, interpreted)[1]


def resolved(task, interpreted):
    """显式 prompt 参数补齐未指定字段，不能覆盖 JSON 中已有的实际要求。"""
    value, differences = compose(task, interpreted)
    if differences:
        raise ValueError('prompt 与客户 JSON 的实际要求冲突')
    return value
