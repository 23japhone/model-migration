"""保留调用方服务参数，为 spawn/fork 派生进程初始化同一组挂点。"""
import os
from pathlib import Path
import runpy
import sys


def main():
    root = Path(__file__).resolve().parent.parent
    os.environ['VERIFY_EAGER_ROOT'] = str(root)
    # Python multiprocessing.spawn 会重放主模块并继承 sys.path。
    sys.path.insert(0, str(root))
    from adapters.install import enable
    enable()
    method = sys.argv.pop(1)
    if method == 'module':
        runpy.run_module('vllm.entrypoints.openai.api_server', run_name='__main__', alter_sys=False)
    else:
        from vllm.entrypoints.cli.main import main as cli
        sys.argv[0] = 'vllm'
        cli()


if __name__ in ('__main__', '__mp_main__'):
    if __name__ == '__main__':
        main()
    else:
        from adapters.install import enable
        enable()
