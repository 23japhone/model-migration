"""将客户声明与运行时解析分开；逐项冻结默认值及其证据。"""
import copy

from coordination.runtime import sources
from ..tensor_alignment import tolerance_change


def begin(session, task):
    previous = session.previous or {}
    declared = previous.get('declared_inputs', previous.get('effective_inputs', task))
    if declared != task and not tolerance_change(declared, task):
        raise ValueError('续接不能改变已冻结的 prompt 实际参数')
    session.value['declared_inputs'] = copy.deepcopy(task)
    session.value['effective_inputs'] = copy.deepcopy(task)
    session.value['default_resolutions'] = copy.deepcopy(previous.get('default_resolutions', {}))
    session.save()


def check_prior(old, value):
    if old.get('invalidated'):
        raise ValueError('默认值冻结绑定已失效，必须使用新 run-id')
    for source in old['evidence']:
        if sources.ref(source['path']) != source:
            raise ValueError('默认值冻结来源证据已变化，必须使用新 run-id')
    if any(old[key] != value[key] for key in ('values', 'identity')):
        raise ValueError('已冻结的默认值或资产/主机身份变化，必须使用新 run-id')


def bind(session, task, key, value):
    bindings = session.value.setdefault('default_resolutions', {})
    old = bindings.get(key)
    if old is not None:
        try:
            check_prior(old, value)
        except (ValueError, OSError):
            old['invalidated'] = True
            session.save()
            raise
    bindings[key] = copy.deepcopy(value)
    effective = session.value.setdefault('effective_inputs', copy.deepcopy(task))
    for field, selected in value['values'].items():
        group, name = field.split('.')
        task[group][name] = copy.deepcopy(selected)
        effective[group][name] = copy.deepcopy(selected)
    session.step('default_' + key, 'completed', value=value)
