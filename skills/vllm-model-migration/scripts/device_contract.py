"""Ascend 设备证据的公开静态文件合同；不执行设备或服务操作。"""
import copy
import re

from device_evidence import bound, ref, require
from allocation_contract import load as load_allocation

RT = 'ASCEND_RT_VISIBLE_DEVICES'
VISIBLE = 'ASCEND_VISIBLE_DEVICES'
SELECTORS = (RT, VISIBLE)


def ids(value):
    require(isinstance(value, str) and re.fullmatch(r'[0-9]+(?:,[0-9]+)*', value),
            '设备环境变量须为非空、非负编号列表')
    result = list(map(int, value.split(',')))
    require(len(result) == len(set(result)), '设备环境变量包含重复编号')
    return result


def rows(value):
    require(isinstance(value, list) and bool(value), '缺少设备映射')
    for row in value:
        require(set(row) == {'physical_id', 'logical_id', 'visible_index'}, '设备映射字段无效')
        require(all(type(v) is int and v >= 0 for v in row.values()), '设备映射编号无效')
    for key in ('physical_id', 'logical_id', 'visible_index'):
        require(len({row[key] for row in value}) == len(value), '设备映射不是一一对应')
    require([row['visible_index'] for row in value] == list(range(len(value))), '进程可见索引不连续')
    return value


def resource_binding(resource):
    return {key: copy.deepcopy(resource[key]) for key in ('kind', 'ownership', 'nonce', 'identity')}


def runtime_change(original, effective, devices, ownership='owned'):
    require({k: v for k, v in original.items() if k != 'env'} ==
            {k: v for k, v in effective.items() if k != 'env'}, '设备解析改写了非环境运行配置')
    before, after = original['env'], effective['env']
    require({k: v for k, v in before.items() if k not in SELECTORS} ==
            {k: v for k, v in after.items() if k not in SELECTORS}, '设备解析改写了非设备环境')
    for key in SELECTORS:
        require(key not in before or before[key] == after.get(key), '显式设备环境冲突：' + key)
    selectors_match(after, effective['kind'], devices, ownership)


def selectors_match(after, kind, devices, ownership):
    if RT in after:
        require(ids(after[RT]) == [row['logical_id'] for row in devices], '运行时环境与逻辑设备映射不一致')
    if VISIBLE in after and kind == 'docker' and ownership == 'owned':
        require(set(ids(after[VISIBLE])) == {row['physical_id'] for row in devices},
                '容器物理设备注入与本次选择不一致')


def probe_binding(mapping):
    base, visible = (bound(mapping[key]) for key in ('base_probe', 'visible_probe'))
    require(base.get('status') == visible.get('status') == 'completed', '设备探针未成功')
    require(type(visible.get('count')) is int and visible['count'] == len(mapping['devices']), '设备探针数量不符')
    rows(base['devices'])
    require(base['library'] == visible['library'] == mapping['library'], 'CANN 库身份变化')
    require(base['executable_realpath'] == visible['executable_realpath'] == mapping['executable_realpath'],
            '设备探针解释器变化')
    require(visible['devices'] == mapping['devices'], '进程索引与映射证据不一致')
    pairs = {(row['physical_id'], row['logical_id']) for row in base['devices']}
    require(all((row['physical_id'], row['logical_id']) in pairs for row in mapping['devices']),
            '基础映射与最终进程映射不一致')
    return visible


def load(handoff, original=None):
    require(all(key in handoff for key in ('declared_request', 'device_mapping', 'effective_runtime')),
            '缺少设备映射证据，请重新执行部署验证')
    declared = bound(handoff['declared_request'])
    load_allocation(declared)
    if original is not None:
        require(declared == original, '设备证据未绑定原始验证请求')
    mapping, effective = (bound(handoff[key]) for key in ('device_mapping', 'effective_runtime'))
    require(mapping.get('schema_version') == effective.get('schema_version') == '1.0', '设备证据版本无效')
    require(mapping.get('format') == 'ascend-device-mapping-v1' and
            effective.get('format') == 'ascend-effective-runtime-v1', '设备证据版本不支持')
    require(mapping['request'] == effective['request'] == handoff['declared_request'] and
            effective['device_mapping'] == handoff['device_mapping'], '设备证据来源引用不一致')
    require(mapping['resource'] == resource_binding(handoff['resource']), '设备证据载体身份不一致')
    require(mapping['resource']['kind'] == declared['runtime']['kind'] and
            mapping['resource']['ownership'] == declared['service']['ownership'], '载体类型或所有权与原请求不符')
    require(mapping['host'].get('boot_id') and mapping['host'].get('hostname'), '缺少宿主启动身份')
    devices = rows(mapping['devices'])
    require(mapping['selected_physical_devices'] == declared['npu']['devices'] and
            {r['physical_id'] for r in devices} == set(declared['npu']['devices']), '设备证据超出本次选卡')
    require(handoff['runtime'] == declared['runtime'] and handoff['model'] == declared['model'] and
            handoff['endpoint'] == declared['service']['endpoint'], '设备证据与部署交接声明不一致')
    runtime_change(declared['runtime'], effective['runtime'], devices, handoff['resource']['ownership'])
    visible = probe_binding(mapping)
    selectors = {k: v for k, v in effective['runtime']['env'].items() if k in SELECTORS}
    require(visible['environment'] == selectors, '有效环境与最终探针不一致')
    return declared, mapping, effective['runtime']


def effective_runtime(handoff, original=None):
    return load(handoff, original)[2]
