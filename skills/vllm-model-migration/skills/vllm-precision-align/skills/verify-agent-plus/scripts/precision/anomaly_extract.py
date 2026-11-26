"""从评测 HTTP 记录里提取异常检测所需的 (请求, 响应) 样本。

数据来源是评测期间的回环记录（http-capture/）：request.bin 存完整原始请求，
response.bin 存原始响应；index.json 按请求顺序登记，与 task.sample_ids 同序。
"""

from pathlib import Path

from .anomaly_detect import collect_anomalies, load_profiles
from .evidence import load as _load_json
from .http_evidence import raw_files


def response_view(body):
    """把原始响应归一化为 {content, tool_calls, reasoning_content}。

    chat 响应取 choices[0].message；completions 响应取 choices[0].text（无 tools/reasoning）。
    """
    choices = body.get('choices')
    choice = choices[0] if isinstance(choices, list) and choices else {}
    message = choice.get('message')
    if isinstance(message, dict):
        return {'content': message.get('content'),
                'tool_calls': message.get('tool_calls'),
                'reasoning_content': message.get('reasoning_content') or message.get('reasoning')}
    return {'content': choice.get('text'), 'tool_calls': None, 'reasoning_content': None}


def samples(root, sample_ids):
    """读取 http-capture，返回 [(sample_id, request, response), ...]。

    request 为完整原始请求（含 messages/tools/tool_choice/采样参数）；
    response 为归一化的 {content, tool_calls, reasoning_content}。
    """
    folder = Path(root) / 'http-capture'
    index = _load_json(folder / 'index.json')
    records = index.get('requests', [])
    result = []
    for position, record in enumerate(records):
        request, response = raw_files(folder, record, {})
        sample_id = sample_ids[position] if position < len(sample_ids) else position
        result.append((sample_id, request, response_view(response)))
    return result


def _profiles_path():
    """解析 verify-agent-plus/references/anomaly-profiles.json 的绝对路径。"""
    return Path(__file__).resolve().parents[2] / 'references' / 'anomaly-profiles.json'


def from_evaluation(prepared, handoff):
    """评测后从各任务 HTTP 记录提取并判定异常样本。

    prepared: prepare() 产物，tasks 每项含 output_root、sample_ids、capture_required；
    handoff: 部署交接，含 endpoint、model.name。
    返回异常样本清单；无 HTTP 记录或记录不可用的任务跳过，不阻塞评测。
    """
    profiles = load_profiles(_profiles_path())
    service = {'endpoint': handoff['endpoint'], 'model': handoff['model']['name']}
    result = []
    for task in prepared['tasks']:
        if not task.get('capture_required'):
            continue  # 未开启回环记录，无请求/响应可判定
        try:
            triples = samples(task['output_root'], task['sample_ids'])
        except (OSError, ValueError, KeyError, TypeError):
            continue  # 记录不可用则跳过；异常检测是可选预筛，不让它打断评测
        result.extend(collect_anomalies(profiles, triples, service))
    return result
