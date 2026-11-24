"""异常样本结构：评测后按判定口径产出可定界的异常样本清单。

每条异常样本的契约字段：
  sample_id     int    评测样本 id（对应 task.sample_ids）
  anomaly_type  str    异常类型，见 references/anomaly-profiles.json
  request       dict   完整原始请求（messages/tools/tool_choice/采样参数）
  service       dict   服务标识 {endpoint, model}

异常类型不在此写死，由 anomaly-profiles.json 的 anomalies[].type 决定，
新增异常类型只需在配置里加一条，无需改动本模块。
"""

FIELDS = ('sample_id', 'anomaly_type', 'request', 'service')


def _service(value):
    """校验服务标识 {endpoint, model}；不合法抛 ValueError。"""
    if not isinstance(value, dict) or set(value) != {'endpoint', 'model'}:
        raise ValueError('service 须为 {endpoint, model} 对象')
    if not isinstance(value['endpoint'], str) or not value['endpoint'].startswith(('http://', 'https://')):
        raise ValueError('服务端点无效')
    if not isinstance(value['model'], str) or not value['model']:
        raise ValueError('模型名无效')
    return value


def validate(value):
    """校验异常样本结构；不合法抛 ValueError，合法则原样返回。"""
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise ValueError('异常样本字段缺失或含未知字段')
    if type(value['sample_id']) is not int or value['sample_id'] < 0:
        raise ValueError('样本 id 须为非负整数')
    if not isinstance(value['anomaly_type'], str) or not value['anomaly_type']:
        raise ValueError('异常类型无效')
    if not isinstance(value['request'], dict) or not value['request']:
        raise ValueError('原始请求须为非空对象')
    _service(value['service'])
    return value


def sample(sample_id, anomaly_type, request, service):
    """构造一条异常样本记录；字段不合法时抛 ValueError。"""
    return validate({'sample_id': sample_id, 'anomaly_type': anomaly_type,
                     'request': request, 'service': service})
