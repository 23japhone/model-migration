"""允许使用 arm 时已核实的引擎终止证据封存，保留尚未完成的前向编号。"""
from ....evidence import read_bound
from ..adapters.state import read


def confirmed(group):
    path = group / 'engine-dead.json'
    if not path.exists():
        return False
    config = read(group / 'group.json')
    event = read(path)
    ref = config['engine_at_arm']
    if ref['path'] != str(group.parent / 'engine.json') or 'async_queue' not in config:
        raise ValueError('缺少 arm 时的异步引擎身份')
    engine = read_bound(ref)
    if (event['group_id'] != group.name or event['signal'] != 'ENGINE_CORE_DEAD'
            or event['engine'] != {k:engine[k] for k in ('pid', 'start_ticks', 'namespace')}):
        raise ValueError('引擎终止事件与请求组身份不同')
    return True
