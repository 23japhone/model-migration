"""跨任务输入关联；不替代目标资产、注册或运行检查。"""
from pathlib import Path

from .evidence import bound,digest
from .receipts import completed,common


def preparation(reference,precheck,verify):
    value = completed(reference,'vllm-migration-prepare','preparation')
    if any(value['conclusions'].get(k)!='pass' for k in ['assets','version_materials']):raise ValueError('准备资产或版本材料不完整')
    assets = {}
    for name in ['model','image']:
        item = value['tasks'][name]
        if item['status']!='completed' or item['conclusions'].get('assets')!='pass':raise ValueError('准备必需任务未完成：'+name)
        assets[name] = item['asset']
    model = assets['model'];actual = verify['model']
    if model['scope']!='full' or model['path']!=actual['root'] or model['path']!=precheck['model']['path']:
        raise ValueError('准备模型范围/位置与迁移输入不符')
    for row in model['files']:
        if actual['files'].get(row['path'])!=row['sha256']:raise ValueError('准备模型文件与运行清单不符')
    if model['task']!=precheck['model']['task'] or set(model['modalities'])!=set(precheck['model']['modalities']):
        raise ValueError('准备任务/模态与预检不符')
    for key in ['vllm','vllm_ascend']:
        if value['inputs']['target'].get(key)!=precheck['target'].get(key):raise ValueError('准备目标版本不符')
    versions(precheck['target'],verify['runtime']['versions'])
    capabilities(precheck,verify)
    return assets


def selected(precheck,verify):
    details = precheck['details'];route = details['route']
    if route not in ('supported_candidate','registration_required','adaptation_required','blocked'):raise ValueError('未知预检路线')
    if route=='blocked':return route
    architecture = details['selected_architecture']
    rows = [r for r in details['support_matrix'] if r['architecture']==architecture]
    if not architecture or len(rows)!=1 or rows[0]['route']!=route:raise ValueError('预检选中候选与矩阵不一致')
    if precheck['conclusions'].get('version_materials')!='pass':raise ValueError('预检版本证据未通过')
    expected = verify['model']['files'][verify['model']['config']]
    if details['model']['config_sha256']!=expected:raise ValueError('预检与运行config身份不一致')
    return route


def adaptation(reference,precheck,verify,runtime_roots,register):
    value = completed(reference,'adapt-agent','adaptation')
    if value.get('operation')!='check':raise ValueError('仅接受check适配交接')
    details = value['details'];context = details['context']
    if details.get('errors') or context.get('errors'):raise ValueError('适配交接仍包含错误')
    original = bound(context['precheck'])
    common(original,'vllm-model-precheck')
    for key in ['selected_architecture','route','sources']:
        if original['details'].get(key)!=precheck['details'].get(key):raise ValueError('适配预检来源/架构与当前阶段不一致')
    if original['inputs'].get('target')!=precheck['inputs'].get('target'):raise ValueError('适配目标版本与当前预检不一致')
    if context['model']['config_sha256']!=precheck['details']['model']['config_sha256']:raise ValueError('适配原始config身份不符')
    capture = bound(value['inputs']['context'])
    for name,source in capture['inputs']['sources'].items():
        files = details['sources'][name]['files']
        for change in details['sources'][name]['changes']:
            if not change['after']:raise ValueError('当前运行契约不能证明适配删除项已生效')
            path = str(Path(runtime_roots[name])/change['path'])
            if registry_derivative(change,register):continue
            if verify['runtime']['files'].get(path)!=change['after']['sha256']:
                raise ValueError('运行载体清单缺少对应路径及内容：'+change['path'])
        if not files:raise ValueError('适配缺少完整来源树')
    return value


def registration(request,precheck,verify,in_container):
    architecture = precheck['details']['selected_architecture']
    if request['model']['architecture']!=architecture:raise ValueError('注册架构与预检不一致')
    if in_container:
        if request['model']['config']!=str(Path(verify['model']['runtime_root'])/verify['model']['config']):raise ValueError('注册载体config路径不符')
    elif digest(request['model']['config'])!=precheck['details']['model']['config_sha256']:raise ValueError('注册config内容不符')
    for key in ['vllm','vllm_ascend']:
        if request['target'].get(key)!=precheck['inputs']['target'].get(key):raise ValueError('注册目标版本不符')


def carrier_request(register,verify,result):
    check = bound(register)
    target = bound({'path':verify['registration']['request'],'sha256':digest(verify['registration']['request'])})
    if check['mode']!=target['mode']:raise ValueError('开发与载体注册方式不一致')
    if check['mode']=='plugin':
        if any(check['plugin'][k]!=target['plugin'][k] for k in ['name','version','entrypoint']):
            raise ValueError('开发与载体plugin名称/版本/entrypoint不一致')
    final = result['details'].get('effect',{}).get('registry_sha256',check['target']['registry_sha256'])
    if target['target']['registry_sha256']!=final:raise ValueError('载体注册不是本次注册结果的内容')
    for key in ['model','implementation']:
        # 载体内路径可以变化，architecture/class/module/内容身份不能变化。
        fields = ['architecture'] if key=='model' else ['module','class_name','sha256']
        if any(check[key].get(k)!=target[key].get(k) for k in fields):raise ValueError('开发注册与运行注册实现不一致')


def versions(target,installed):
    for key,alias in [('vllm','vllm'),('vllm_ascend','vllm-ascend')]:
        wanted = target[key];actual = installed.get(alias,installed.get(key))
        if not isinstance(actual,str):raise ValueError('运行请求缺少目标版本：'+alias)
        if (actual if '+' in wanted else actual.split('+')[0])!=wanted:raise ValueError('运行版本与预检不符：'+alias)


def registry_derivative(change,register):
    return (register.get('mode')=='source' and change['path'].endswith('/model_executor/models/registry.py')
            and register['target']['registry_sha256']==change['after']['sha256'])


def capabilities(precheck,verify):
    model = precheck['model'];modalities = set(model['modalities'])
    if modalities-{'text','image'}:raise ValueError('请求模态尚无阶段验收实现')
    feature = {'chat':'chat','generate':'text'}.get(model['task'])
    required = set(verify['required_features'])
    if feature not in required or ('image' in modalities and 'image' not in required):
        raise ValueError('运行必需用例未覆盖请求task/modalities')
    if set(precheck.get('features',['eager']))-{'eager'} or model.get('quantization') is not None:
        raise ValueError('请求能力超出本阶段eager非量化验收范围，不能静默降级')
