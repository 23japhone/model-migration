"""有界的单条 completions 请求及事后只读查询入口。"""
from contextlib import contextmanager
import fcntl
from http.client import HTTPException
from pathlib import Path
import re
import uuid

from ... import outputs, request as schema
from ...evidence import begin, finish, read_bound, safe_text, write_json
from ...protocol.http import exchange
from .adapters.state import read
from .index import prediction, query, selected
from .service import deployment, live
from .tensors import file_ref


def validate(request, action):
    keys = ['source', 'request_id']
    schema.fields(request, keys + (['prompt'] if action == 'capture-one' else []),
                  ['output_index', 'max_tokens', 'allowed_token_ids'] if action == 'capture-one' else ['output_index', 'layer', 'api', 'occurrence'])
    ref = request['source']
    schema.fields(ref, ['path', 'sha256'])
    schema.absolute(ref['path'])
    if not schema.HASH.fullmatch(ref['sha256']) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,96}', request['request_id']):
        raise ValueError('无效的回执摘要或请求 ID')
    index = request.get('output_index', 1)
    if type(index) is not int or index < 0:
        raise ValueError('输出序号须为非负整数')
    if action == 'capture-read':
        for field in ('layer', 'api'):
            if field in request and (not isinstance(request[field], str) or not request[field]):
                raise ValueError('层路径与 API 名称须为非空字符串')
        if type(request.get('occurrence', 0)) is not int or request.get('occurrence', 0) < 0:
            raise ValueError('API 调用序号须为非负整数')
    if action == 'capture-one':
        validate_generation(request, index)


def validate_generation(request, index):
    maximum = request.get('max_tokens', 2)
    if type(maximum) is not int or not 0 <= index < maximum or not isinstance(request['prompt'], str) or not request['prompt'].strip():
        raise ValueError('需要非空文本和覆盖查询序号的 max_tokens')
    ids = request.get('allowed_token_ids', [0])
    if not isinstance(ids, list) or not ids or any(type(i) is not int or i < 0 for i in ids):
        raise ValueError('无效的选词限制')


@contextmanager
def exclusive(root):
    with (root / 'control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (root / 'active.json').exists() or (root / 'lease.json').exists():
            raise ValueError('前一次采集尚有活动请求')
        yield


def generate(declared, request, client_id, folder):
    folder.mkdir()
    payload = {'model': declared['model']['name'], 'prompt': request['prompt'], 'temperature': 0,
               'max_tokens': request.get('max_tokens', 2), 'n': 1, 'request_id': client_id, 'return_token_ids': True}
    if 'allowed_token_ids' in request:
        payload['allowed_token_ids'] = request['allowed_token_ids']
    response, _ = exchange(declared['service']['endpoint'], '/v1/completions', payload, folder,
                           declared['service']['timeout_seconds'], payload['model'], declared.get('api_key_env'))
    if (response['id'] != 'cmpl-' + client_id or response['model'] != payload['model']
            or len(response['choices']) != 1):
        raise ValueError('HTTP 响应 ID 或选择数与本次请求不符')
    return response['choices'][0]


def capture(request, declared, handoff, prep, attempt):
    root = Path(prep['config']['path']).parent
    with exclusive(root):
        ready = live(handoff, prep, attempt)
        folder = root / ('request-' + uuid.uuid4().hex)
        folder.mkdir(mode=0o700)
        write_json(attempt / 'capture-session.json', {'path': str(folder), 'request_id': request['request_id']})
        write_json(folder / 'intent.json', request)
        native = {**read_bound(prep['config']), 'dump_path': str(Path(prep['dump']['runtime_path']) / 'native'),
                  'dump_enable': True}
        config = root / 'capture-config.json'
        if config.exists() and read(config) != native:
            raise ValueError('派生 msProbe 配置已变更')
        if not config.exists():
            write_json(config, native)
        ref = file_ref(config, root)
        baseline = generate(declared, request, uuid.uuid4().hex, folder / 'control')
        write_json(root / 'active.json', {'directory': folder.name})
        try:
            reply = generate(declared, request, request['request_id'], folder / 'http')
        finally:
            (root / 'active.json').unlink(missing_ok=True)
        live(handoff, prep, attempt)
        read_bound(ref)
        errors = list(folder.glob('error-*.json'))
        if errors:
            raise ValueError(read(errors[0])['error'])
        mapped = read(folder / 'request.json')
        tokens = reply['token_ids']
        if (baseline['token_ids'] != tokens or mapped['prompt_ids'] != reply['prompt_token_ids']
                or baseline['prompt_token_ids'] != mapped['prompt_ids']):
            raise ValueError('开关采集的实际前文或输出序列不同')
        index = request.get('output_index', 1)
        if len(tokens) <= index:
            raise ValueError(f"请求仅输出 {len(tokens)} 个 Token，结束原因 {reply.get('finish_reason')}")
        rows = [prediction(folder, root, ready, mapped, tokens, i) for i in range(len(tokens))]
        if 'allowed_token_ids' in request and not read_bound(rows[index])['scores_changed']:
            raise ValueError('指定的选词限制未改变查询序号的分数')
        receipt = attempt / 'capture-receipt.json'
        write_json(receipt, {'request_id': request['request_id'], 'source': request['source'], 'derived_config': ref,
                            'preparation': handoff['worker_capture']['receipt'], 'control_ids': baseline['token_ids'],
                            'token_ids': tokens, 'outputs': rows,
                            'sampling': file_ref(folder / 'http' / 'request.json', folder),
                            'response': file_ref(folder / 'http' / 'response.json', folder),
                            'engine_input': file_ref(folder / 'request.json', folder), 'response_id': 'cmpl-' + request['request_id'],
                            'worker': file_ref(root / 'worker.json', root), 'engine': file_ref(root / 'engine.json', root)})
        return file_ref(receipt, attempt)


def run(request, target, run_id):
    declared, handoff, prep = deployment(request['source'])
    outputs.check(target, outputs.protected_roots(declared))
    attempt, manifest = begin(target, request, run_id, 'capture-one')
    try:
        receipt = capture(request, declared, handoff, prep, attempt)
        row = query(receipt, request['request_id'], request.get('output_index', 1))
        manifest['detailed_report'] = {'receipt': receipt, 'forward_id': row['forward_id'], 'token_id': row['token_id'],
                                       'layer_count': len(row['layers']), 'call_count': len(row['calls'])}
        manifest['conclusions']['capture'] = 'pass'
        return finish(manifest, 'completed')
    except (OSError, HTTPException, RuntimeError, KeyError, ValueError, TypeError, IndexError, KeyboardInterrupt) as error:
        manifest['diagnostic'] = {'message': safe_text(error), 'request_id': request['request_id']}
        manifest['detailed_report']['error'] = safe_text(error)
        manifest['conclusions']['capture'] = 'fail'
        return finish(manifest, 'failed')


def lookup(request, target, run_id):
    row = query(request['source'], request['request_id'], request.get('output_index', 1))
    return 0, selected(row, request)
