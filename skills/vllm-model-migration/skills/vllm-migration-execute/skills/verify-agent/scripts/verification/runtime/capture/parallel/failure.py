"""记录正式请求的 HTTP 失败及已产生的证据，结束当前租约。"""
from ....evidence import write_json
from ..tensors import file_ref
from ..adapters.state import read


def inference_failed(request, folder, intent, response):
    if read(folder / 'intent.json')['request_id'] != request['trace_id']:
        raise ValueError('失败请求的 trace 不匹配')
    ended = read(folder / 'frontend-end.json')
    if ended['trace_id'] != request['trace_id']:
        raise ValueError('失败前端的请求身份不匹配')
    files = [file_ref(p, folder) for p in sorted(folder.glob('rank*/end-*.json'))]
    path = folder / 'failed-request.json'
    result = {'status': 'failed', 'inference_status': 'failed', 'trace_id': request['trace_id'],
              'http': request['http'], 'diagnostic': response['error'], 'forwards': files,
              'sample_key': intent['sample_key'], 'native_id': intent['native_id']}
    write_json(path, result)
    return {**result, 'receipt': file_ref(path, folder)}
