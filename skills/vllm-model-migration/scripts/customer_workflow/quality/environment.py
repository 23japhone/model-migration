"""质量复测的版本选择独立于普通测试，按需委托公开安装入口。"""
from pathlib import Path
import sys
import uuid

from coordination.runtime import sources
from coordination.runtime.process import call
from skill_paths import skill_root
from ..state import identity
from .resolution import publish


def environment(source, context, version, root):
    observed = source.get('evaluator_environment', context.get('evaluator', {}))
    if observed.get('version') == version:
        return observed
    if 'evaluator_environment' in source:
        raise ValueError('基线显式绑定的评测环境版本不同')
    pin = root / 'environment-selection.json'
    if pin.exists():
        report = sources.bound(sources.read(pin))
    else:
        work = root / 'installation' / uuid.uuid4().hex
        work.mkdir(parents=True)
        venv = root / 'environments' / ('evalscope-' + identity(version)[:16])
        request = {'operation': 'evalscope_environment', 'install': {'venv': str(venv), 'version': version}}
        request_path = root / 'requests' / (identity(request) + '.json')
        publish(request_path, request)
        args = [sys.executable, '-B', str(skill_root('vllm-migration-prepare') / 'scripts/customer.py'),
                '--request', str(request_path), '--work-root', str(work), '--run-id', 'quality-environment']
        code, out = call(args, 2100, work, 'install')
        result = sources.parse(out)
        if code or result.get('status') != 'completed':
            raise ValueError('质量 EvalScope 独立环境准备失败：' + str(result.get('reason', work)))
        path = Path(result['manifest'])
        if path.resolve() != path or not path.is_relative_to(work):
            raise ValueError('环境准备返回的回执路径不属于本轮')
        reference = sources.ref(path)
        report = sources.bound(reference)
        if report['inputs'] != request:
            raise ValueError('质量评测环境安装请求与回执不一致')
        publish(pin, reference)
    actual = report['result']
    if actual.get('versions', {}).get('evalscope') != version or report['status'] != 'completed':
        raise ValueError('评测安装回执与本次固定的 EvalScope 版本不符')
    return {'python': actual['python'], 'version': version}
