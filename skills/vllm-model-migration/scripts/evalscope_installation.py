"""EvalScope 安装参数的共享内部契约；供客户编排与准备 CLI 使用。"""
from pathlib import Path
import sys

from skill_paths import plugin_root

STRINGS = ('python', 'venv', 'version')
FLAGS = {'upgrade': '--upgrade', 'dry_run': '--dry-run', 'help': '--help'}


def validate(value):
    if not isinstance(value, dict) or value.keys() - {*STRINGS, *FLAGS}:
        raise ValueError('evalscope_install 必须为仅含安装参数的对象')
    for name in STRINGS:
        if name in value:
            item = value[name]
            if not isinstance(item, str) or not item.strip() or '\0' in item or item.startswith('-'):
                raise ValueError('安装参数 ' + name + ' 必须为非空字符串，不能以 - 开头')
    for name in FLAGS:
        if name in value and type(value[name]) is not bool:
            raise ValueError('安装参数 ' + name + ' 必须为布尔值')
    return dict(value)


def normalize(value):
    result = {'python': sys.executable, 'venv': '/data/evalscope-venv', **validate(value)}
    result['venv'] = str(Path(result['venv']).expanduser().resolve())
    return result


def arguments(options):
    argv = ['--python', options['python'], '--venv', options['venv']]
    if 'version' in options:
        argv.extend(['--version', options['version']])
    return argv + [flag for name, flag in FLAGS.items() if options.get(name, False)]


def preview(options):
    return options.get('help', False) or options.get('dry_run', False)


def protect(options, roots):
    target = Path(options['venv']).resolve()
    for root in [plugin_root(), *roots]:
        root = Path(root).resolve()
        if target.is_relative_to(root) or root.is_relative_to(target):
            raise ValueError('共享 EvalScope venv 不得与 Skill、输入、资产或证据路径重叠')


def legacy(parent):
    """旧准备回执只识别并拒绝，不再复用或重新解释。"""
    import json
    if (parent / 'evalscope-selection.json').exists():
        raise ValueError('旧 EvalScope 环境回执要求新 run-id')
    for path in parent.glob('environment-selection-*.json'):
        if 'evalscope' in json.loads(path.read_text()):
            raise ValueError('旧 EvalScope 安装回执要求新 run-id')
