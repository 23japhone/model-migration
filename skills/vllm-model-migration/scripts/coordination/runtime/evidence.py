"""独立尝试和终态发布；共享写入失败立即停止后续效果。"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import signal
from pathlib import Path
import tempfile
import uuid

from .sources import ref
from ..planning import CONCLUSIONS


class EvidenceError(OSError):
    """不能保证证据完整，禁止继续执行阶段。"""


@contextmanager
def defer_interrupts(received):
    """短收尾区间只记录信号；调用者必须将取消落入失败终态。"""
    previous = {}
    try:
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, lambda signum, frame: received.append(signum))
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def mark_cancelled(value, received):
    value.update(status='failed', success=False, next_action='inspect_resources_after_cancellation')
    value.setdefault('terminal_cancellation', []).extend(received)


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, value):
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix='.entry-', dir=path.parent)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
        os.replace(temporary, path)
    except OSError as error:
        raise EvidenceError('总入口证据保存失败') from error
    finally:
        try:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        except OSError as error:
            raise EvidenceError('总入口临时证据清理失败') from error


def begin(request, parent, run_id, action, source):
    attempt = parent/uuid.uuid4().hex
    attempt.mkdir(parents=True, exist_ok=False)
    value = {'schema_version': '1.0', 'skill_version': '0.5.0-dev.1',
             'task_id': 'vllm-model-migration', 'run_id': run_id, 'operation': action,
             'inputs': request, 'request_source': source, 'status': 'running', 'success': False,
             'started_at': now(), 'stages': {}, 'results': {}, 'resources': [],
             'conclusions': {key: 'not_evaluated' for key in CONCLUSIONS},
             'evidence': {key: str(attempt/name) for key, name in (
                 ('manifest','entry-manifest.json'), ('ledger','task-ledger.json'),
                 ('handoff','entry-handoff.json'), ('summary','summary.md'))},
             'uncovered': ['未执行性能验收；总入口不重新评分或证明阶段未测量的能力'], 'next_action': 'running'}
    write(attempt/'entry.request.json', request)
    return attempt, value


def save(attempt, value):
    write(attempt/'task-ledger.json', value['stages'])


def publish_snapshot(attempt, value):
    value['finished_at'] = now()
    save(attempt, value)
    handoff = {'schema_version': '1.0', 'task_id': value['task_id'], 'run_id': value['run_id'],
               'status': value['status'], 'ledger': ref(attempt/'task-ledger.json'),
               'stages': value['stages'], 'conclusions': value['conclusions'],
               'results': value['results'], 'resources': value['resources'],
               'lifecycle': value.get('lifecycle')}
    write(attempt/'entry-handoff.json', handoff)
    value['handoff'] = ref(attempt/'entry-handoff.json')
    text = '# 总入口运行摘要\n\n执行状态：'+value['status']+'；成功：'+str(value['success'])+'。\n\n'
    text += '\n'.join('- '+key+'：'+verdict for key, verdict in value['conclusions'].items())+'\n\n'
    text += '下一步：'+value['next_action']+'。服务状态以来源观察时间为准。\n'
    write(attempt/'summary.md', text)
    write(attempt/'.entry-manifest.pending.json', value)
    code = 0 if value['success'] else 3 if value['status'] == 'blocked' else 1
    return code, {'manifest': value['evidence']['manifest'], 'status': value['status'],
                  'conclusions': value['conclusions'], 'next_action': value['next_action']}


def commit_manifest(attempt, value, received):
    """检查待决取消后进入不可取消提交区；rename 原子发布，区内新信号随后递送。"""
    signals = {signal.SIGINT, signal.SIGTERM}
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
    try:
        pending = signals.intersection(signal.sigpending())
        if (received or pending) and not value.get('terminal_cancellation'):
            received.extend(sorted(pending))
            return False
        try:
            os.replace(attempt/'.entry-manifest.pending.json', attempt/'entry-manifest.json')
        except OSError as error:
            raise EvidenceError('总入口终态发布失败') from error
        return True
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def publish(attempt, value):
    received = []
    with defer_interrupts(received):
        result = publish_snapshot(attempt, value)
        if not commit_manifest(attempt, value, received):
            mark_cancelled(value, received)
            result = publish_snapshot(attempt, value)
            commit_manifest(attempt, value, received)
    return result
