"""正式测量开始前，按发现时绑定的归档身份重核发布来源、数据集与各项 GPU 成绩。"""
from alignment.evidence import EvidenceError, digest, safe
from alignment.request import reference
from .children import PublicSourceError


def verify_ref(item):
    reference(item)
    if digest(item['path']) != item['sha256']:
        raise ValueError('已固定的 GPU 证据内容变化：' + item['path'])


def verify_attachments(evidence):
    for key in ('gpu_receipt', 'gpu_config', 'gpu_report'):
        verify_ref(evidence[key])


def verify_scope(checked, request):
    if checked['protocol']['dataset'] != request['baseline']['dataset']:
        raise ValueError('公开数据集与发现时的记录不同')
    baseline = request['baseline']
    if any(item.get('subset') is None for item in baseline['scores']):
        scopes = checked['protocol']['subsets']
        if set(baseline.get('subset_list', scopes)) != set(scopes):
            raise ValueError('所声明的公开总分范围与 GPU 归档不同')
    scores = {(row['metric'], row['subset']): row for row in checked['scores']}
    for item in request['baseline']['scores']:
        if item.get('gpu_score') is None:
            continue  # 发现时已记录的公开缺项随其他可核验指标一并交付。
        name = item['metric']
        scope = (name, item.get('subset'))
        score = scores.get(scope, {})
        if 'error' in score or score.get('value') is None or score['value'] != item['gpu_score']:
            raise ValueError('GPU 原生成绩与发现时的记录不同：' + str(scope))


def recheck(request, inspect):
    if request.get('baseline_source') != 'publisher':
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
        raise PublicSourceError('正式测量前无法重新核实自动 GPU 基线：' + safe(error)) from error
