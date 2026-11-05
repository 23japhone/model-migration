"""一次合成环境选择；操作开关不继承，问题仅阻塞环境消费者。"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil

from evalscope_installation import STRINGS, normalize, validate, preview
from . import history


@dataclass
class Plan:
    options: dict
    selection: dict | None = None
    problem: str | None = None
    preview_requested: bool = False

    @property
    def is_preview(self):
        return self.preview_requested or preview(self.options)


def python_path(value):
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return os.path.abspath(candidate)
    found = shutil.which(value)
    if found:
        return os.path.abspath(found)
    raise ValueError('指定的基础 Python 不可用：' + value)


def differences(options, expected):
    changed = {key: {'previous': expected.get(key), 'requested': options.get(key)}
               for key in STRINGS if expected.get(key) != options.get(key)}
    if changed:
        return '安装选择变化，要求新 run-id：' + json.dumps(changed, ensure_ascii=False)
    return None


def resolve(current, previous=None):
    current = validate(current)
    previous = previous or {}
    try:
        old = history.selected(previous)
    except history.Unavailable as error:
        return Plan({}, problem=str(error), preview_requested=preview(current))
    expected = dict(old['options']) if old else {}
    binding = previous.get('evalscope_environment', {})
    if binding:
        expected['version'] = binding['version']
    options = normalize({**expected, **current})
    if old and current.get('venv', expected['venv']) == expected['venv']:
        options['venv'] = expected['venv']  # 历史路径重定向属于实际变化，不是本次选择。
    try:
        options['python'] = python_path(options['python'])
    except (OSError, ValueError) as error:
        change = differences(options, expected) if old else None
        return Plan(options, old, change or str(error))
    problem = differences(options, expected) if old else None
    if binding.get('invalidated'):
        problem = '实际环境绑定已失效，要求新 run-id'
    return Plan(options, old, problem)
