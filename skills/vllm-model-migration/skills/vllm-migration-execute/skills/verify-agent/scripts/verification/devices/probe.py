"""最终载体独立 ctypes 探针：仅查询，不 set_device、不分配设备内存。"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys

RT = 'ASCEND_RT_VISIBLE_DEVICES'
VISIBLE = 'ASCEND_VISIBLE_DEVICES'
POINTER = ctypes.POINTER(ctypes.c_int32)
SIGNATURES = {
    'aclInit': [ctypes.c_char_p], 'aclFinalize': [],
    'aclrtGetDeviceCount': [ctypes.POINTER(ctypes.c_uint32)],
    'aclrtGetVersion': [POINTER, POINTER, POINTER],
    'aclrtGetLogicDevIdByPhyDevId': [ctypes.c_int32, POINTER],
    'aclrtGetPhyDevIdByLogicDevId': [ctypes.c_int32, POINTER],
    'aclrtGetLogicDevIdByUserDevId': [ctypes.c_int32, POINTER],
}


def call(library, report, name, *args):
    code = int(getattr(library, name)(*args))
    report['calls'].append({'api': name, 'return_code': code})
    if code != 0:
        raise ValueError(name + ' failed: ' + str(code))


def translated(library, report, name, value):
    result = ctypes.c_int32(-1)
    call(library, report, name, value, ctypes.byref(result))
    if result.value < 0:
        raise ValueError(name + ' returned negative device ID')
    return result.value


def library_identity(library, report):
    paths = set()
    for line in Path('/proc/self/maps').read_text().splitlines():
        parts = line.split(maxsplit=5)
        if len(parts) == 6 and Path(parts[5]).name.startswith('libascendcl.so'):
            paths.add(str(Path(parts[5]).resolve()))
    if len(paths) != 1:
        raise ValueError('cannot identify unique loaded libascendcl.so')
    path = paths.pop()
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    versions = [ctypes.c_int32() for _ in range(3)]
    call(library, report, 'aclrtGetVersion', *(ctypes.byref(v) for v in versions))
    return {'path': path, 'sha256': digest.hexdigest(), 'api_version': [v.value for v in versions]}


def observe(library, report):
    count = ctypes.c_uint32()
    call(library, report, 'aclrtGetDeviceCount', ctypes.byref(count))
    if not 0 < count.value <= 1024:
        raise ValueError('invalid visible device count: ' + str(count.value))
    report['count'] = count.value
    for index in range(count.value):
        logical = translated(library, report, 'aclrtGetLogicDevIdByUserDevId', index)
        physical = translated(library, report, 'aclrtGetPhyDevIdByLogicDevId', logical)
        inverse = translated(library, report, 'aclrtGetLogicDevIdByPhyDevId', physical)
        if inverse != logical:
            raise ValueError('physical/logical round trip mismatch')
        report['devices'].append({'physical_id': physical, 'logical_id': logical, 'visible_index': index})


def probe(request):
    report = {'status': 'blocked', 'devices': [], 'calls': [], 'environment': {},
              'executable_realpath': str(Path(sys.executable).resolve())}
    library = None
    initialized = False
    try:
        # 清除仅影响本探针的旧筛选；必须发生在加载CANN前。
        for key in (RT, VISIBLE):
            os.environ.pop(key, None)
            if key in request['environment']:
                os.environ[key] = request['environment'][key]
        if request['mode'] == 'base':
            os.environ.pop(RT, None)
        report['environment'] = {key: os.environ[key] for key in (RT, VISIBLE) if key in os.environ}
        library = ctypes.CDLL('libascendcl.so')
        for name, signature in SIGNATURES.items():
            function = getattr(library, name)
            function.argtypes, function.restype = signature, ctypes.c_int32
        call(library, report, 'aclInit', None)
        initialized = True
        report['library'] = library_identity(library, report)
        observe(library, report)
        report['status'] = 'completed'
    except (OSError, AttributeError, ValueError, KeyError, TypeError) as error:
        report['diagnostic'] = {'type': type(error).__name__, 'message': str(error)}
    finally:
        if initialized:
            try:
                call(library, report, 'aclFinalize')
            except ValueError as error:
                report.update(status='blocked', finalize_error=str(error))
    return report


if __name__ == '__main__':
    print('VLLM_DEVICE_JSON=' + json.dumps(probe(json.load(sys.stdin))))
