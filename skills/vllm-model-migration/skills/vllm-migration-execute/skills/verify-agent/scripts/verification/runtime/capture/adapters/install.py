"""按目标实际模块摘要安装挂点。普通部署可在适配器不匹配时继续。"""
import hashlib
from importlib.machinery import PathFinder
from pathlib import Path
import sys

from . import engine, state, worker, frontend
from .sources import SOURCES
from . import parallel


def attach(module):
    name = module.__name__
    try:
        actual = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
        if actual not in SOURCES[name]:
            raise ValueError('未适配的目标源码 ' + name + ' ' + actual)
        if name == 'vllm_ascend.compilation.acl_graph':
            from .aclgraph import hooks
            hooks.install(module)
        elif name == parallel.EXECUTOR:
            parallel.executor(module.MultiprocExecutor)
        elif name.endswith('.scheduler'):
            engine.scheduler(module.Scheduler)
        elif name.endswith('.input_processor'):
            engine.input_processor(module.InputProcessor)
        elif name.endswith('.model_runner_v1'):
            worker.install(module.NPUModelRunner)
            if (state.root() / 'graph.json').is_file():
                from .aclgraph.hooks import install_calibration
                install_calibration(module.NPUModelRunner)
        elif name == 'vllm.entrypoints.openai.completion.serving':
            frontend.install(module.OpenAIServingCompletion, 'create_completion')
        elif name == 'vllm.entrypoints.openai.chat_completion.serving':
            frontend.install(module.OpenAIServingChat, 'create_chat_completion')
        state.write(state.root() / ('hook-' + name + '.json'), {'module': name, 'sha256': actual})
    except Exception as error:
        state.fail(state.root(), error)


class Hooks:
    def find_spec(self, fullname, path, target=None):
        if fullname not in SOURCES:
            return None
        spec = PathFinder.find_spec(fullname, path)
        if spec and spec.loader:
            execute = spec.loader.exec_module
            def run(module):
                execute(module)
                attach(module)
            spec.loader.exec_module = run
        return spec


def enable():
    if any(isinstance(entry, Hooks) for entry in sys.meta_path):
        return
    if (state.root() / 'graph.json').is_file():
        from .aclgraph.hooks import MODULE, DIGEST
        SOURCES[MODULE] = (DIGEST,)
    if (state.root() / 'parallel.json').is_file():
        SOURCES[parallel.EXECUTOR] = (parallel.EXECUTOR_SHA,)
    sys.meta_path.insert(0, Hooks())
    for name in SOURCES.keys() & sys.modules.keys():
        attach(sys.modules[name])
