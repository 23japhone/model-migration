"""将独立静态事实汇总为支持矩阵和迁移路线。"""
from .review import conclusion, platform_conclusion


def choose_route(request, facts):
    model = facts['model']
    registry = facts['registry']
    reasons = diagnostics(facts)
    versions = conclusion(facts['reviews'], 'version_pair')
    if versions != 'pass':
        reasons.append('目标版本配套资料未通过')
    architectures = model.get('architectures', [])
    matrix = [candidate(request, facts, architecture) for architecture in architectures]
    selected = selected_candidate(matrix)
    if reasons or not registry.get('complete') or selected is None or selected['route'] == 'blocked':
        reasons.extend(item['reason'] for item in matrix if item['route'] == 'blocked')
        return {'route': 'blocked', 'selected_architecture': None, 'support_matrix': matrix,
                'reasons': reasons or ['没有可识别且证据齐备的模型候选'], 'version_materials': versions}
    return {'route': selected['route'], 'selected_architecture': selected['architecture'],
            'support_matrix': matrix, 'reasons': [], 'version_materials': versions}


def diagnostics(facts):
    reasons = list(facts['errors'])
    for name in ('model', 'registry', 'optional'):
        reasons.extend(facts[name].get('errors', []))
    reasons.extend('资料记录无效：' + item['error'] for item in facts['reviews'] if not item['validated'])
    return reasons


def selected_candidate(matrix):
    # 框架优先使用第一个已注册架构；不得为了 pass 偷换成后面的实现。
    registered = next((item for item in matrix if item['registered']), None)
    return registered or next((item for item in matrix if item['route'] != 'blocked'), None)


def candidate(request, facts, architecture):
    entry = next((item for item in facts['registry'].get('entries', [])
                  if item['architecture'] == architecture), {'registered': False})
    platform = platform_conclusion(facts['reviews'], request, architecture)
    route, reason = architecture_route(architecture, entry, platform, facts['optional'])
    return {'architecture': architecture, 'registered': entry['registered'],
            'platform_support': platform, 'route': route, 'reason': reason,
            'implementation': entry.get('implementation')}


def architecture_route(architecture, entry, platform, optional):
    implementation = optional['implementation']
    adaptation = optional['adaptation']
    if 'error' in entry:
        return 'blocked', '已注册实现无法核对：' + entry['error']
    if platform == 'review_required':
        return 'blocked', '平台资料未覆盖该架构及全部请求能力'
    if entry['registered'] and platform == 'pass':
        return 'supported_candidate', '目标源码已注册且本次平台资料通过，仍需运行验证'
    if not entry['registered'] and platform == 'pass' and implementation:
        if implementation['architecture'] == architecture:
            return 'registration_required', '已有实现未进入目标注册映射'
    if adaptation and adaptation['class_name'] == architecture:
        if adaptation_allowed(entry['registered'], platform, adaptation['allowed_scope']):
            return 'adaptation_required', '存在具体参考实现和允许范围，需完成适配及真实验证'
    return 'blocked', '实现、平台或允许适配范围不足，不能进入部署'


def adaptation_allowed(registered, platform, scope):
    model_ready = registered or 'model' in scope
    platform_ready = platform == 'pass' or (platform == 'fail' and 'platform' in scope)
    return model_ready and platform_ready
