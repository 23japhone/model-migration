"""成员与真实前端、引擎身份的绑定，路径只使用登记过的成员名。"""
from contextlib import contextmanager
import fcntl
import hashlib
import os

from .. import state


def active():
    if 'VERIFY_EAGER_ROOT' not in os.environ:
        return None
    root = state.root()
    try:
        group_id = state.read(root / 'active.json').get('group_id')
    except FileNotFoundError:
        return None
    return root / group_id if group_id else None


def member(group, trace):
    if trace in state.read(group / 'group.json')['traces']:
        return group / ('member-' + trace)
    return None


def route(group, kind, identity):
    key = hashlib.sha256(identity.encode()).hexdigest()
    return group / (kind + '-' + key + '.json')


def bind(group, kind, identity, trace):
    # link publishes an immutable mapping atomically, including across processes.
    path = route(group, kind, identity)
    source = group / ('member-' + trace) / 'identity.json'
    try:
        os.link(source, path)
    except FileExistsError:
        raise ValueError('同一引擎身份重复绑定 ' + kind) from None


def resolve(group, kind, identity):
    try:
        return state.read(route(group, kind, identity))['trace_id']
    except FileNotFoundError:
        return None


@contextmanager
def admission(group):
    with (group / 'admission.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def frontend_folder(trace):
    group = active()
    if group is None:
        return state.active()
    folder = member(group, trace)
    if folder:
        with admission(group):
            if group != active():
                return None
            try:
                (folder / 'claimed').mkdir()
            except FileExistsError:
                state.fail(folder, '同一 trace 重复进入前端')
                return None
    return folder


def publish_frontend(folder, trace, external):
    if (folder / 'identity.json').is_file():
        try:
            bind(folder.parent, 'external', external, trace)
        except (ValueError, OSError) as error:
            state.fail(folder, error)
            return None
    if (folder / 'frontend.json').exists():
        raise ValueError('一道题出现多次或并行引擎请求')
    state.write(folder / 'frontend.json', {'trace_id': trace, 'external_id': external})
    return folder, trace


def engine_folder(request):
    group = active()
    if group is None:
        return state.active()
    if not request.external_req_id:
        return None
    trace = resolve(group, 'external', request.external_req_id)
    if trace:
        folder = member(group, trace)
        try:
            bind(group, 'internal', request.request_id, trace)
        except (ValueError, OSError) as error:
            state.fail(folder, error)
            return None
        return folder
    return None
