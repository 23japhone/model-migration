"""固定真实两端执行的软件身份，使不同版本的 GPU/NPU 包不能混用。"""

SIDES = ('gpu','npu')


def pair(sources, target, scope, prior):
    if scope is not None:
        return scope.get('runtime_versions', {})
    inventory = sources.find('tensor-inventory-v1', model_identity=target['model_identity'])
    if inventory:
        return {value['hardware']:value['versions'] for _,value in inventory if 'versions' in value}
    return (prior or {}).get('runtime_versions', {})


def missing(versions, kinds):
    if not isinstance(versions, dict) or versions.keys() - set(SIDES):
        raise ValueError('清单 runtime_versions 应按 gpu、npu 标明实际版本')
    issues = []
    for side in SIDES:
        if side in versions or kinds[side] == 'runtime':
            record = versions.get(side)
            if not isinstance(record, dict) or not all(isinstance(record.get(key),str) and record[key].strip()
                                                      for key in ('torch','vllm','msprobe')):
                issues.append('缺少 ' + side + ' 的 torch、vllm、msprobe 实际版本')
    return issues
