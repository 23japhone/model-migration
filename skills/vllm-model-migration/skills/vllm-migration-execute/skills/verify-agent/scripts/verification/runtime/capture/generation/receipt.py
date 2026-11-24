"""Close delivery, sampler and image provenance against the accepted engine tokens."""
import hashlib
import json
import math

from ....evidence import read_bound
from ..adapters.state import read
from .vision import bind_images, images


def generation_evidence(folder, payload, response, timeline, errors, http):
    result = {}
    captured = folder / 'frontend-payload-digest.json'
    if captured.exists():
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        frontend = read(captured)
        if digest != frontend['sha256'] or frontend.get('http_sha256', http['request']['sha256']) != http['request']['sha256']:
            errors.append('前端收到的 HTTP 请求与封存原件不同')
    elif payload.get('messages') and any(isinstance(m.get('content'), list) for m in payload['messages']):
        errors.append('缺少前端 HTTP 图文输入摘要')
    if response.get('model') and response['model'] != payload.get('model'):
        errors.append('HTTP model 身份不符')
    if 'delivery' in response:
        result['delivery'] = link_delivery(response, timeline, errors)
    sources = images(folder, payload)
    if sources:
        result['images'] = bind_images(sources, timeline, errors)
        confirm_processor(folder, result['images'], errors)
    check_sampling_rows(payload, timeline, errors)
    return result


def matches_sample(value, payload, accepted):
    if not value or type(payload.get('seed')) != int or value['seed'] != payload['seed']:
        return False
    if value['candidate'] != accepted or not math.isclose(value['temperature'], payload['temperature'], rel_tol=1e-5):
        return False
    expected_k = payload.get('top_k', -1)
    actual_k = value.get('top_k')
    return (expected_k == actual_k or expected_k in (-1, 0) and actual_k is None) and math.isclose(
        value.get('top_p') or 1.0, payload.get('top_p', 1.0), rel_tol=1e-5)


def check_sampling_rows(payload, timeline, errors):
    if payload.get('temperature', 0) > 0:
        for entry in timeline:
            if entry['output_index'] is None:
                continue
            ref = entry['ranks'][0].get('row')
            value = read_bound(ref).get('sampling_decision', {}) if ref else {}
            if not matches_sample(value, payload, entry['token_id']):
                errors.append(f"{entry['forward_id']} rank0 缺少与请求 seed 一致的实际随机采样")


def confirm_processor(folder, sources, errors):
    features = read(folder / 'request.json').get('mm_features', [])
    if len(features) != len(sources):
        errors.append('图像数与引擎接收的处理器结果数不符')
        return
    for image, feature in zip(sources, features):
        if any(member['feature'] != feature for member in image['ranks']):
            errors.append(f"图像 {image['image_index']} 处理器与 worker 特征不符")


def link_delivery(response, timeline, errors):
    delivery = response['delivery']
    accepted = [row for row in timeline if row['output_index'] is not None]
    tokens = [row['token_id'] for row in accepted]
    delivered = response['choices'][0]['token_ids']
    aligned = True
    for event in delivery['events']:
        indices = event['output_indices']
        aligned = aligned and event['token_ids'] == [tokens[i] for i in indices if i < len(tokens)]
        event['forward_ids'] = [accepted[i]['forward_id'] for i in indices] if aligned else []
    if not aligned or delivery['complete'] and delivered != tokens:
        errors.append('SSE Token 与接受序列不符或缺少 return_token_ids')
        aligned = False
    if not delivery['complete']:
        errors.append(delivery.get('error', 'SSE 交付未完整结束'))
    return {**delivery, 'delivered_token_ids': delivered, 'token_alignment': 'verified_prefix' if aligned else 'incomplete'}
