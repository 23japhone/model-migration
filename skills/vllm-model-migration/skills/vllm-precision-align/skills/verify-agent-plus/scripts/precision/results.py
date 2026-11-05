"""读取固定版本的 JSONL 预测和 JSON 评分，完整保留原始指标。"""
from collections import Counter
import json
import math
from pathlib import Path

from .evidence import decode, load, sha256
from .http_evidence import check_capture


def prediction_errors(row):
    errors=[]
    if row.get('is_success') is False or row.get('success') is False:
        errors.append('推理请求失败')
    if row.get('error') or row.get('error_info'):
        errors.append('预测含错误记录')
    if row.get('finish_reason')=='length':errors.append('生成被长度截断')
    if 'prediction' not in row or not isinstance(row['prediction'],str):
        errors.append('缺少实际生成文本')
    return errors


def predictions(path):
    rows=[];errors=[]
    try:
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                row=decode(line)
                if not isinstance(row,dict) or type(row.get('id'))!=int:
                    raise ValueError('预测 id 无效')
                rows.append(row);errors.extend(prediction_errors(row))
    except (OSError,ValueError,TypeError) as error:
        errors.append('预测读取失败：'+str(error))
    return rows,errors


def metric_value(raw, key, spec):
    value=raw
    for part in spec.get('path',[key]):
        value=value.get(part) if isinstance(value,dict) else None
    if type(value) not in (int,float) or not math.isfinite(value):return None
    limit={'percent':100,'ratio':1}.get(spec['unit'])
    if limit is not None and not 0<=value<=limit:return None
    return value


def coverage(rows,task,errors):
    counts=Counter(row['id'] for row in rows)
    expected=set(task['sample_ids'])
    missing=sorted(expected-set(counts));extra=sorted(set(counts)-expected)
    duplicate=sorted(key for key,count in counts.items() if count!=1)
    if missing or extra or duplicate:errors.append('样本覆盖不完整、重复或越界')
    return {'expected':len(expected),'observed':len(rows),'missing':missing,'extra':extra,'duplicate':duplicate}


def score_file(result_path,task,errors):
    raw={}
    try:
        raw=load(result_path)
        if not isinstance(raw,dict):raise ValueError('评分结果不是对象')
        if raw.get('error'):errors.append('评分器报告错误')
    except (OSError,ValueError,TypeError) as error:
        errors.append('评分读取失败：'+str(error));raw={}
    metrics={}
    for key,spec in task['metrics'].items():
        value=metric_value(raw,key,spec)
        metrics[key]={**spec,'value':value}
        if value is None:errors.append('未取得有效指标：'+key)
    return raw,metrics


def read_task(root, task):
    root=Path(root);abbr=task['model_abbr'];dataset=task['dataset']
    candidates=list((root/'aisbench').glob('*/predictions')) if (root/'aisbench').is_dir() else []
    native=candidates[0].parent if len(candidates)==1 else root
    pred=native/'predictions'/abbr/(dataset+'.jsonl')
    result_path=native/'results'/abbr/(dataset+'.json')
    rows,errors=predictions(pred)
    covered=coverage(rows,task,errors)
    raw,metrics=score_file(result_path,task,errors)
    files={str(path.relative_to(root)):sha256(path) for path in (pred,result_path) if path.is_file()}
    fingerprint=None
    if task.get('capture_required'):
        failures,fingerprint=check_capture(root,files,rows,task);errors.extend(failures)
    return {'dataset':dataset,'model_abbr':abbr,'valid':not errors,'metrics':metrics,
            'coverage':covered,'request_conditions_sha256':fingerprint,
            'errors':errors,'files':files,'native_details':raw.get('details'), 'output_root':str(root)}
