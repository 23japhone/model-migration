"""独立评测解释器入口；分析和计划不加载本入口。"""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def main(argv):
    from precision.evidence import load
    if argv[0] == 'preflight':
        from precision.runtime.evalscope.data import inspect
        inspect(load(argv[1]), argv[2])
    elif argv[0] == 'probe':
        from precision.runtime.evalscope.probe import freeze
        freeze(load(argv[1]), argv[2])
    elif argv[0] == 'run':
        from precision.runtime.evalscope.configuration import native_config
        from evalscope import run_task
        run_task(native_config(load(argv[1])))
    else:
        raise ValueError('未知评测操作')


if __name__ == '__main__':
    main(sys.argv[1:])
