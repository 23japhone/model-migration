"""独立核验尝试的入口与证据；不改写客户任务及安装历史。"""
import json
from pathlib import Path
import subprocess
import uuid

from coordination.cli import Parser, read_request
from coordination.request import output
from coordination.runtime import sources
from coordination.runtime.evidence import EvidenceError, write
from skill_paths import plugin_root
from . import handoff, handoff_probe


def arguments(argv):
    parser = Parser(description='只读核验 EvalScope 性能交接环境；不安装或压测')
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--work-root', type=Path, required=True)
    parser.add_argument('--run-id', required=True, help='必须与来源客户任务一致')
    parser.add_argument('--plan', action='store_true', help='只校验请求形状，不读取引用或写文件')
    return parser.parse_args(argv)


def attempt(value, source, parent, run_id):
    expected, refs, protected = handoff.load(value, run_id)
    sources.protect(parent, [plugin_root(), Path(source['path']), *protected])
    folder = parent / uuid.uuid4().hex
    try:
        folder.mkdir(parents=True)
    except OSError as error:
        raise EvidenceError('交接核验证据目录不可写') from error
    manifest = folder / 'handoff-check.json'
    result = {'status': 'running', 'run_id': run_id, 'request_source': source, 'sources': refs,
              'manifest': str(manifest), 'new_run_required': False}
    write(folder / 'request.json', value)
    write(manifest, result)
    code = 3
    try:
        checked = handoff_probe.check(expected, folder)
        # 检查期间引用不可变化；只发布本次仍可复核的交接。
        if sources.bound(source) != value or handoff.load(value, run_id)[0] != expected:
            raise ValueError('核验期间交接来源发生变化')
        result.update(status='ready', performance_handoff=checked)
        code = 0
    except EvidenceError:
        raise
    except handoff_probe.Changed as error:
        result.update(status='blocked', reason=str(error) + '，要求新 run-id',
                      reason_kind='identity_changed', new_run_required=True)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        result.update(status='blocked', reason=str(error), reason_kind='unverifiable')
    except KeyboardInterrupt:
        result.update(status='failed', reason='核验取消，禁止性能启动', reason_kind='cancelled')
        code = 1
    write(manifest, result)
    return code, result


def execute(args):
    try:
        request_path = args.request.expanduser().absolute()
        value = handoff.request(read_request(request_path))
        parent = output(args.work_root, args.run_id).parent / 'evalscope-handoff'
        sources.protect(parent, [plugin_root(), request_path])
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, RecursionError) as error:
        return 2, {'status': 'invalid_input', 'reason': str(error)}
    if args.plan:
        return 0, {'status': 'not_run', 'inputs': value,
                   'uncovered': ['仅校验请求形状；未读取引用、核验环境或执行性能测试']}
    try:
        source = sources.ref(request_path)
        if sources.bound(source) != value:
            raise ValueError('请求在校验后发生变化')
        return attempt(value, source, parent, args.run_id)
    except sources.OutputError as error:
        return 2, {'status': 'invalid_input', 'reason': str(error)}
    except EvidenceError as error:
        return 1, {'status': 'failed', 'reason': str(error)}
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, RecursionError) as error:
        return 3, {'status': 'blocked', 'reason': str(error), 'reason_kind': 'evidence_invalid'}
    except KeyboardInterrupt:
        return 1, {'status': 'failed', 'reason': '核验取消；未完成环境交接'}


def main(argv=None):
    code, result = execute(arguments(argv))
    print(json.dumps(result, ensure_ascii=False))
    return code
