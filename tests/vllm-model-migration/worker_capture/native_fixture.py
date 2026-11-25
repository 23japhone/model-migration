"""可核算的合成 worker 落盘。张量为测试字节，不代表 PyTorch/NPU 推理产物。"""
import json
import struct
import uuid

from .session_fixture import write


MODULES = ['model.embed_tokens', 'model.layers.0', 'model.layers.1', 'model.norm', 'lm_head']


def native_dump(runtime, step):
    folder = runtime / 'dump' / 'native' / f'step{step}' / 'rank0'
    data, parents = {}, {}
    for module in MODULES:
        call = f'Module.{module}.Block.forward.0'
        data[call] = tensors(folder, call)
        parents[call] = None
        if module.startswith('model.layers.'):
            for occurrence in range(2):
                api = f'Torch.add.{module.rsplit(".", 1)[-1]}{occurrence}.forward'
                data[api] = tensors(folder, api)
                parents[api] = call
    write(folder / 'dump.json', {'data': data})
    write(folder / 'construct.json', parents)
    return folder, list(data)


def tensors(folder, call):
    value = {}
    for side in ('input_args', 'output'):
        name = call + '-' + side + '.pt'
        path = folder / 'dump_tensor_data' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(struct.pack('<2f', 1, 2))
        value[side] = [{'shape': [1, 2], 'dtype': 'torch.float32', 'data_name': name}]
    return value


def frame(runtime, folder, internal, step, output_index, token, prompt=(11, 22, 33), preceding=71, forward=None):
    dump, calls = native_dump(runtime, step)
    value = {'internal_id': internal, 'forward_id': uuid.uuid4().hex, 'output_index': output_index,
        'batch_row': 0, 'step': step, 'dump': str(dump.relative_to(runtime)),
        'input_ids': list(prompt) if output_index == 0 else [preceding],
        'positions': list(range(len(prompt))) if output_index == 0 else [len(prompt) + output_index - 1],
        'logits_indices': [len(prompt)-1] if output_index == 0 else [0], 'candidate': token,
        'coverage': {'calls': {'input': calls, 'output': calls},
                     'modules': {'begin': MODULES, 'end': MODULES},
                     'scope': 'all_invoked_modules_and_msprobe_registered_apis'}}
    write(folder / ('begin-' + value['forward_id'] + '.json'), value)
    if forward:
        forward(folder, value)
    for name in ('raw', 'scores'):
        values = [0.0] * 100
        values[3 if name == 'raw' else token] = 10.0
        filename = f'{step}-{name}.bin'
        (folder / filename).write_bytes(struct.pack('<100f', *values))
        value[name] = {'file': filename, 'size': 100, 'dtype': 'torch.float32'}
    write(folder / (value['forward_id'] + '.json'), value)
    write(folder / f'prediction-{output_index}.json', value)
    write(folder / ('end-' + value['forward_id'] + '.json'), {**value, 'status': 'completed'})
    write(folder / f'accepted-{output_index}.json', {'internal_id': internal,
        'forward_id': value['forward_id'], 'output_index': output_index, 'token_id': token, 'finish_reason': None})


def respond(runtime, trace, tokens=(71, 92), prompt=(11, 22, 33), forward=None):
    active = json.loads((runtime / 'active.json').read_text())
    folder = runtime / active['directory']
    external, internal = 'actual-' + trace, 'actual-' + trace + '-random-suffix'
    write(folder / 'frontend.json', {'trace_id': trace, 'external_id': external, 'response_id': 'reply-' + trace})
    write(folder / 'request.json', {'external_id': external, 'internal_id': internal, 'prompt_ids': list(prompt)})
    write(folder / 'engine-output.json', {'external_id': external, 'prompt_ids': list(prompt),
                                          'token_ids': list(tokens), 'finish_reason': 'stop'})
    for index, token in enumerate(tokens):
        step = len(list((runtime / 'dump/native').glob('step*'))) + 1
        frame(runtime, folder, internal, step, index, token, prompt, tokens[index-1], forward)
    write(folder / 'frontend-end.json', {'trace_id': trace})
    return {'id': 'reply-' + trace, 'model': 'synthetic-model',
            'choices': [{'index': 0, 'text': 'synthetic output', 'finish_reason': 'stop'}]}
