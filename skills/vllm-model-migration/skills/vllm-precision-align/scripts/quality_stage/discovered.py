"""正式测量开始前，按发现时绑定的归档身份重核原生条件与各项 GPU 成绩。"""
from alignment.evidence import EvidenceError, digest, safe
from alignment.request import reference
from .children import ConditionsMismatch


def verify_ref(item):
    reference(item)
    if digest(item['path']) != item['sha256']:
        raise ValueError('已固定的 GPU 证据内容变化：' + item['path'])


def verify_attachments(evidence):
    # 公开检查器允许不依赖逐题 reviews 的总分比较。自动发现的归档还须保留其引用的附件。
    for key, item in evidence.items():
        if key == 'gpu_reviews':
            for review in item.values():
                verify_ref(review)
        else:
            verify_ref(item)


def verify_scope(checked, request):
    native = request['native_request']
    if checked['static_conditions'] != native['expected_static_conditions']:
        raise ValueError('GPU 静态条件与发现时的记录不同')
    expected = native['protocol']
    protocol = checked['protocol']
    for key, value in expected.items():
        if key != 'metrics' and protocol.get(key) != value:
            raise ValueError('GPU/NPU 冻结评测协议不同：' + key)
    scores = {(row['metric'], row['subset']): row for row in checked['scores']}
    for item in request['baseline']['scores']:
        name = item['metric']
        if not expected['metrics'].get(name) or protocol['metrics'].get(name) != expected['metrics'][name]:
            raise ValueError('GPU 指标口径与请求不同：' + name)
        scope = (name, item.get('subset'))
        score = scores.get(scope, {})
        if 'error' in score or score.get('value') is None or score['value'] != item['gpu_score']:
            raise ValueError('GPU 原生成绩与发现时的记录不同：' + str(scope))


def recheck(request, inspect):
    if 'expected_static_conditions' not in request['native_request']:
        return
    try:
        source = request.get('gpu_evidence')
        if not source:
            raise ValueError('缺少自动发现的 GPU 归档引用')
        verify_ref(source)
        checked = inspect(source['path'])
        references = checked['evidence']
        if references.get('gpu_evidence') != source:
            raise ValueError('GPU 归档与发现时固定的来源不同')
        verify_attachments(references)
        verify_scope(checked, request)
    except EvidenceError:
        raise
    except (ValueError, KeyError, TypeError, OSError) as error:
        raise ConditionsMismatch('正式测量前无法重新核实自动 GPU 基线：' + safe(error)) from error
