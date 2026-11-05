"""逐用例隔离协议失败，必需功能及语义分别聚合。"""
import json
import http.client
from pathlib import Path

from .http import exchange
from .usage import require_prompt, validate as validate_usage
from ..evidence import safe_text, sha256, write_json


def validate_response(response, model):
    if not isinstance(response, dict) or response.get('model') != model or 'error' in response:
        raise ValueError('响应 model 身份不一致或返回错误')
    choices = response.get('choices')
    if not isinstance(choices, list) or len(choices) != 1:raise ValueError('需要唯一完整生成 choice')
    item = choices[0]
    if not isinstance(item, dict) or not isinstance(item.get('message', {}), dict):
        raise ValueError('choice/message 结构无效')
    text = item.get('text', item.get('message', {}).get('content'))
    if item.get('index', 0) != 0 or not isinstance(text, str) or not text.strip():
        raise ValueError('HTTP 200 但生成为空或 choice 无效')
    if item.get('finish_reason') != 'stop':raise ValueError('生成没有正常完整结束')
    return {'text': text, 'finish_reason': 'stop', 'usage': validate_usage(response.get('usage'))}


def semantic(case, text):
    expected = case['semantic']
    if expected['mode'] == 'review':return 'review_required'
    return 'pass' if text.strip() in expected['expected'] else 'fail'


def execute_one(request, case, attempt):
    folder = attempt / 'cases' / case['id']
    folder.mkdir(parents=True)
    result = {'id': case['id'], 'kind': case['kind'], 'required': case['required'],
              'protocol': 'fail', 'semantic': 'not_evaluated'}
    try:
        response, elapsed = exchange(request['service']['endpoint'], case['route'], case['payload'], folder,
                                     request['service']['timeout_seconds'], request['model']['name'],
                                     request.get('api_key_env'))
        checked = response if case['kind'] == 'stream' else validate_response(response, request['model']['name'])
        require_prompt(checked.get('usage'), case.get('min_prompt_tokens', 0), case.get('expected_prompt_tokens'))
        result.update(checked, protocol='pass', semantic=semantic(case, checked['text']), elapsed_seconds=elapsed)
        evidence = folder / ('assembled.json' if case['kind'] == 'stream' else 'response.json')
        raw = folder / ('response.sse' if case['kind'] == 'stream' else 'response.json')
        result['response'] = {'path': str(evidence), 'sha256': sha256(evidence),
                              'raw_path': str(raw), 'raw_sha256': sha256(raw)}
    except (OSError, ValueError, TypeError, KeyError, IndexError, TimeoutError, http.client.HTTPException) as error:
        result['error'] = safe_text(error)
    write_json(folder / 'result.json', result)
    return result


def execute(request, attempt):
    results = [execute_one(request, case, attempt) for case in request['cases']]
    images = [c for c in results if c['required'] and c['kind'] == 'image' and c['protocol'] == 'pass']
    if len(images) >= 2 and len({v['text'].strip() for v in images}) != len(images):
        for image in images:image.update(semantic='fail', error='差异图片得到相同生成，不能通过图像差异用例')
    write_json(attempt / 'cases.json', results)
    return results


def conclusions(results):
    required = [r for r in results if r['required']]
    protocol = 'pass' if required and all(r['protocol'] == 'pass' for r in required) else 'fail'
    values = {r['semantic'] for r in required}
    value = 'fail' if 'fail' in values or protocol == 'fail' else ('review_required' if 'review_required' in values else 'pass')
    return {'functional_protocol': protocol, 'semantic': value}
