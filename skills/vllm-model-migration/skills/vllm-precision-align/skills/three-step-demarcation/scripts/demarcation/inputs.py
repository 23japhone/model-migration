"""绑定服务与坏样本引用为实际可执行输入；只读核验引用内容身份。"""
from .evidence import bound
from .probe.capability import root_endpoint


def bind(request):
    """返回 {'endpoint', 'model', 'badcase'}；badcase 须为 JSON 对象（messages 是否合法
    由第 1 步静态校验判定，空 messages 应作为致命 REQUEST 结论而非在此拒绝）。"""
    badcase = bound(request['badcase'])
    if not isinstance(badcase, dict):
        raise ValueError('badcase 必须为 JSON 对象')
    service = request['service']
    return {'endpoint': root_endpoint(service['endpoint']),
            'model': service.get('model'),
            'badcase': badcase}
