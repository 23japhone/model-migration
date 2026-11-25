"""单请求、多 rank、重复 Token 的离线 worker 文件，不启动推理服务。"""
import shutil
import struct
from uuid import uuid4

from worker_capture import native_fixture as native
from worker_capture.session_fixture import write


def add_frame(root, folder, size, step, pos, count, output_index, cache=None):
    fid = uuid4().hex
    identity = dict(internal_id='req', request_id='A', ordinal=step, position=pos, count=count,
                    phase='prefill' if pos < 3 else 'decode', output_index=output_index or 0,
                    schedule_id=uuid4().hex, forward_id=fid)
    write(folder / f'dispatch-{step}.json', identity)
    source, calls = native.native_dump(root, step)
    for rank in range(size):
        dump = source.parent / f'rank{rank}'
        if rank:
            shutil.copytree(source, dump)
        directory = folder / f'rank{rank}'
        directory.mkdir(exist_ok=True)
        frame = {**identity, 'dispatch': identity, 'rank': rank, 'positions': list(range(pos, pos + count)),
                 'input_ids': [7] * count, 'logits_indices': [count - 1], 'step': step, 'probe_window_seconds': 1,
                 'dump': str(dump.relative_to(root)), 'coverage': {
                     'calls': {'input': calls, 'output': calls}, 'modules': {'begin': native.MODULES, 'end': native.MODULES},
                     'scope': 'all_invoked_modules_and_msprobe_registered_apis'}}
        write(directory / f'begin-{fid}.json', frame)
        if cache:
            frame['decode_cache'] = cache(directory, frame)
        write(directory / f'end-{fid}.json', {**frame, 'status': 'completed'})
        if rank == 0 and output_index is not None:
            write_prediction(directory, frame, output_index)
    return fid


def write_prediction(directory, frame, index):
    fid = frame['forward_id']
    vector = [0.0] * 100
    vector[7] = 1
    for key in ('raw', 'scores'):
        name = f'{fid}-{key}.bin'
        (directory / name).write_bytes(struct.pack('<100f', *vector))
        frame[key] = {'file': name, 'size': 100, 'dtype': 'torch.float32'}
    frame['candidate'] = 7
    write(directory / f'{fid}.json', frame)
    write(directory / f'prediction-{index}.json', frame)
    write(directory.parent / f'accepted-{index}.json',
          dict(internal_id='req', forward_id=fid, output_index=index, token_id=7))
