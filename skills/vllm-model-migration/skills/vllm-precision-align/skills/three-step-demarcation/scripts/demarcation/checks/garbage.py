"""原始输出乱码/无意义重复检测；阈值来自 profiles.json。"""
from collections import Counter


def _min_window_diversity(visible, window):
    """滑窗字符多样性最小值；正常文本约 0.3+，乱码重复约 0.1 以下。"""
    if len(visible) <= window:
        return len(set(visible)) / max(1, len(visible))
    best = 1.0
    for start in range(0, len(visible) - window + 1, window):
        best = min(best, len(set(visible[start:start + window])) / window)
    return best


def _dup_ngram_ratio(visible, ngram):
    """字符级 n-gram 重复率；正常生成 <0.2，无意义循环约 0.9+。"""
    if len(visible) < ngram * 2:
        return 0.0
    grams = [visible[i:i + ngram] for i in range(len(visible) - ngram + 1)]
    counts = Counter(grams)
    duplicate = sum(count for count in counts.values() if count > 1)
    return duplicate / len(grams)


def analyze_garbage(text, thresholds):
    """乱码检测（正版口径：原始输出已是乱码、无意义重复或错误 token
    → 模型/推理链路问题）。可见字符过短不判定。"""
    window = thresholds.get('window', 100)
    ngram = thresholds.get('ngram', 8)
    min_visible = thresholds.get('min_visible_chars', 40)
    dup_min_chars = thresholds.get('dup_ngram_min_chars', 200)
    visible = ''.join(ch for ch in text if not ch.isspace())
    size = len(visible)
    if text and not visible:
        return {'checked': True, 'char_diversity': 0.0, 'alnum_ratio': 0.0,
                'top_token_freq': 0.0, 'dup_ngram_ratio': 1.0,
                'is_garbage': True, 'reasons': ['原始输出几乎全为空白/不可见字符']}
    if size < min_visible:
        return {'checked': False, 'is_garbage': False,
                'reason': '可见字符不足 %d, 跳过乱码检测' % min_visible}
    alnum_ratio = sum(ch.isalnum() for ch in visible) / size
    diversity = _min_window_diversity(visible, window)
    tokens = [token for token in text.split() if token]
    top_freq = (Counter(tokens).most_common(1)[0][1] / len(tokens)) \
        if len(tokens) >= 20 else 0.0
    dup_ratio = _dup_ngram_ratio(visible, ngram) if size >= dup_min_chars else 0.0
    reasons = []
    if alnum_ratio < thresholds.get('alnum_ratio', 0.05):
        reasons.append('可见字符几乎全为符号/标点(alnum 占比 %.2f)' % alnum_ratio)
    if diversity < thresholds.get('char_diversity', 0.12):
        reasons.append('字符多样性过低(%.3f)' % diversity)
    if top_freq > thresholds.get('top_token_freq', 0.6):
        reasons.append('最高频 token 占比 %.2f(无意义重复)' % top_freq)
    if dup_ratio > thresholds.get('dup_ngram_ratio', 0.7):
        reasons.append('%d-gram 重复率 %.2f(无意义循环重复)' % (ngram, dup_ratio))
    return {'checked': True,
            'char_diversity': round(diversity, 4),
            'alnum_ratio': round(alnum_ratio, 4),
            'top_token_freq': round(top_freq, 4),
            'dup_ngram_ratio': round(dup_ratio, 4),
            'is_garbage': bool(reasons), 'reasons': reasons}
