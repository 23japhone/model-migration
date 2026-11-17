"""保留调用方服务参数，为 spawn/fork 派生进程初始化同一组挂点。"""
import os
from pathlib import Path
import runpy
import sys


def initialize():
    root = Path(__file__).resolve().parent.parent
    os.environ['VERIFY_EAGER_ROOT'] = str(root)
    # 脚本目录中的 coverage 等私有模块不能遮蔽第三方顶层依赖。
    script_dir = root / 'adapters'
    sys.path[:] = [path for path in sys.path if Path(path or os.getcwd()).resolve() != script_dir]
    if str(root) not in sys.path: sys.path.insert(0, str(root))
    from adapters.install import enable
    enable()


def main():
    method = sys.argv.pop(1)
    if method == 'module':
        runpy.run_module('vllm.entrypoints.openai.api_server', run_name='__main__', alter_sys=False)
    else:
        from vllm.entrypoints.cli.main import main as cli
        sys.argv[0] = 'vllm'
        cli()


if __name__ in ('__main__', '__mp_main__'):
    # spawn 重放主模块时也核对路径，而不是依赖父进程的隐式目录。
    initialize()
    if __name__ == '__main__': main()
