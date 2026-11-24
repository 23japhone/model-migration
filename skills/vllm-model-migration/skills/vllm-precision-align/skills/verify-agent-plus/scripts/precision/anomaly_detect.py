"""异常判定：按 anomaly-profiles.json 的口径判断样本是否为异常。

detect(profiles, request, response) -> list[str]：返回命中的异常类型（enabled 且满足 detect）。
  request  dict  完整原始请求（messages/tools/tool_choice/采样参数）
  response dict  归一化 chat 响应（content/tool_calls/reasoning_content）

判定口径只做「预筛」：命中后交给 three-step-demarcation 做定界出确定结论。
乱码指标（char_diversity / dup_ngram_ratio）与三步定界 garbage 口径保持一致。
"""

from collections import Counter

from .anomalies import sample as _anomaly_sample
from .evidence import load as _load_json


def load_profiles(path):
    """加载 anomaly-profiles.json；结构不合法抛 ValueError。"""
    value = _load_json(path)
    if value.get('schema_version') != '1.0' or not isinstance(value.get('anomalies'), list):
        raise ValueError('异常判定配置无效')
    return value


def _visible(text):
    return ''.join(ch for ch in (text or '') if not ch.isspace())


def _char_diversity(text, window=100):
    """滑窗字符多样性最小值；越低越像乱码/重复（口径同三步定界 garbage）。"""
    visible = _visible(text)
    if not visible:
        return 0.0
    if len(visible) <= window:
        return len(set(visible)) / len(visible)
    best = 1.0
    for start in range(0, len(visible) - window + 1, window):
        best = min(best, len(set(visible[start:start + window])) / window)
    return best


def _dup_ngram_ratio(text, ngram=8):
    """字符级 n-gram 重复率；越高越像无意义循环（口径同三步定界 garbage）。"""
    visible = _visible(text)
    if len(visible) < ngram * 2:
        return 0.0
    grams = [visible[i:i + ngram] for i in range(len(visible) - ngram + 1)]
    counts = Counter(grams)
    duplicate = sum(count for count in counts.values() if count > 1)
    return duplicate / len(grams)


def _empty_tool_calls(detect, request, response):
    """请求带 tools 但 chat 响应无 tool_calls。"""
    return bool(request.get('tools')) and not bool(response.get('tool_calls'))


def _missing_reasoning_content(detect, request, response):
    """输出含 think 标记但 chat 响应缺 reasoning_content。"""
    text = response.get('content') or ''
    has_think = '</think>' in text or ' response' in text
    return has_think and not response.get('reasoning_content')


def _garbage_output(detect, request, response):
    """字符多样性过低或 n-gram 重复过高（阈值来自配置）。"""
    text = response.get('content') or ''
    if len(_visible(text)) < 40:
        return False  # 可见字符过短不判定，交给三步定界
    thresholds = detect.get('thresholds', {})
    if _char_diversity(text) < thresholds.get('char_diversity', 0.12):
        return True
    if _dup_ngram_ratio(text) > thresholds.get('dup_ngram_ratio', 0.7):
        return True
    return False


def _low_score(detect, request, response):
    """分数低于阈值（阈值待定，暂不启用）。"""
    return False


# 异常类型 -> 判定函数；新增类型在此注册 + 配置加一条
_DETECTORS = {
    'empty_tool_calls': _empty_tool_calls,
    'missing_reasoning_content': _missing_reasoning_content,
    'garbage_output': _garbage_output,
    'low_score': _low_score,
}


def detect(profiles, request, response):
    """返回命中的异常类型列表。"""
    hits = []
    for rule in profiles['anomalies']:
        if not rule.get('enabled'):
            continue
        detector = _DETECTORS.get(rule['type'])
        if detector is not None and detector(rule.get('detect', {}), request, response):
            hits.append(rule['type'])
    return hits


def collect_anomalies(profiles, samples, service):
    """把命中的异常样本整理成清单（评测后写进 manifest.anomalies 用）。

    samples: [(sample_id, request, response), ...]，request/response 结构与 detect 一致；
    service: {'endpoint': ..., 'model': ...}。
    返回：[{sample_id, anomaly_type, request, service}, ...]。
    """
    result = []
    for sample_id, request, response in samples:
        for anomaly_type in detect(profiles, request, response):
            result.append(_anomaly_sample(sample_id, anomaly_type, request, service))
    return result
