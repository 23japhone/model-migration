"""核对已阅读的官方资料记录；不把结构检查冒充网页语义验证。"""
from datetime import date
import hashlib
from urllib.parse import urlsplit

from .evidence import read_bytes


BASE_FIELDS = {'topic', 'target', 'url', 'checked_at', 'evidence_file', 'evidence_sha256',
               'conclusion', 'note'}
COVERAGE_FIELDS = {'architectures', 'tasks', 'modalities', 'dtypes', 'quantization', 'features'}


def official_url(url):
    if not isinstance(url, str):
        raise ValueError('资料 URL 无效')
    parsed = urlsplit(url)
    valid = parsed.scheme == 'https' and not parsed.username and not parsed.password
    valid &= not parsed.query and not parsed.fragment and parsed.port in (None, 443)
    host = parsed.hostname or ''
    official = host in {'docs.vllm.ai', 'www.hiascend.com', 'hiascend.com'}
    official |= host == 'github.com' and parsed.path.startswith('/vllm-project/')
    if not valid or not official:
        raise ValueError('资料须来自官方 HTTPS 页面且不含凭据、查询参数或片段')


def inspect_record(record, target):
    if not isinstance(record, dict) or BASE_FIELDS - set(record):
        raise ValueError('资料记录字段不完整')
    if set(record) - BASE_FIELDS - COVERAGE_FIELDS:
        raise ValueError('资料记录含未知字段')
    if record['topic'] not in ('version_pair', 'platform_support') or record['target'] != target:
        raise ValueError('资料主题或目标不一致')
    if record['conclusion'] not in ('pass', 'fail', 'review_required'):
        raise ValueError('资料结论无效')
    checked = date.fromisoformat(record['checked_at'])
    if checked.isoformat() != record['checked_at'] or checked > date.today():
        raise ValueError('checked_at 必须为有效的已发生日期')
    if not isinstance(record['note'], str) or not record['note'].strip():
        raise ValueError('资料须有实际复核说明')
    official_url(record['url'])
    raw = read_bytes(record['evidence_file'])
    if hashlib.sha256(raw).hexdigest() != record['evidence_sha256']:
        raise ValueError('资料原文摘要不一致')
    text = raw.decode('utf-8-sig')
    if not text.strip():
        raise ValueError('资料原文为空')
    if record['topic'] == 'platform_support':
        validate_coverage(record)
    return {**record, 'evidence_text': text, 'validated': True}


def validate_coverage(record):
    for key in COVERAGE_FIELDS:
        value = record.get(key)
        if not isinstance(value, list) or not value:
            raise ValueError('平台资料缺少覆盖声明：' + key)
        for item in value:
            if item is None and key == 'quantization':
                continue
            if not isinstance(item, str) or not item:
                raise ValueError('平台资料覆盖声明无效：' + key)


def covers(record, request, architecture):
    model = request['model']
    expected = {'architectures': [architecture], 'tasks': [model['task']],
                'modalities': model['modalities'], 'dtypes': [model['dtype']],
                'quantization': [model['quantization']], 'features': request['features']}
    return all(set(items).issubset(set(record[key])) for key, items in expected.items())


def inspect_reviews(request):
    output = []
    for index, record in enumerate(request['reviews']):
        try:
            item = inspect_record(record, request['target'])
        except (OSError, ValueError, TypeError, KeyError) as error:
            item = {'index': index, 'validated': False, 'error': str(error)}
        output.append(item)
    return output


def conclusion(records, topic):
    values = [item['conclusion'] for item in records if item['validated'] and item['topic'] == topic]
    if 'fail' in values:
        return 'fail'
    if not values or 'review_required' in values:
        return 'review_required'
    return 'pass'


def platform_conclusion(records, request, architecture):
    matched = [item for item in records if item['validated'] and item['topic'] == 'platform_support'
               and covers(item, request, architecture)]
    return conclusion(matched, 'platform_support')
