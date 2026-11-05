"""资产请求生成；不在总入口下载或重新实现文件核验。"""
import copy
from pathlib import Path
from coordination.runtime import sources
from ..defaults.image import image_request


def preparation(task, observations, session):
    model = copy.deepcopy(observations['model_resolution']['request'])
    explicit = task['model']['path']
    if isinstance(explicit, str) and Path(model['path']).resolve() != Path(explicit).resolve():
        raise ValueError('模型解析结果改变了客户显式路径')
    image = image_request(task, observations, session)
    tasks = {}
    for name, request in (('model', model), ('image', image)):
        ref = session.artifact('prepare-' + name, request)
        tasks[name] = {'request': ref['path'], 'required': True}
    if observations.get('asset_python'):
        tasks['model']['python'] = observations['asset_python']
    return {'purpose': 'deployment', 'tasks': tasks, 'target': observations['target'],
            'version_review': observations['version_review']}


def subsets(dataset, catalog):
    rows = catalog['subsets']
    names = [row['name'] for row in rows]
    if not rows or len(names) != len(set(names)):
        raise ValueError('原生子集目录为空或重复')
    requested = dataset['subset_list']
    if requested == -1:
        return list(rows)
    if set(requested) - set(names):
        raise ValueError('客户所选子集不在原生数据集中')
    mapped = {row['name']: row for row in rows}
    return [mapped[name] for name in requested]


def dataset_request(dataset, catalog, subset):
    request = copy.deepcopy(catalog['preparation'])
    if Path(request['path']).resolve() != Path(dataset['path']).resolve():
        raise ValueError('数据准备必须使用客户指定路径')
    request['split'] = subset['split']
    request['selection'] = {'offset': 0, 'file': subset['source_file']}
    if dataset['limit'] is not None:
        request['selection']['limit'] = dataset['limit']
    if request.get('recipe', 'generic') != 'generic':
        request['subjects'] = [subset['name']] if request['recipe'] != 'gsm8k' else []
    return request


def dataset_jobs(task, observations):
    jobs = []
    for i, dataset in enumerate(task['accuracy']['datasets']):
        catalog = observations['datasets'][dataset['name']]
        native_files = None
        if task['accuracy']['evaluator'] == 'evalscope':
            native_files = native_catalog(task, dataset, catalog, observations).get('data_files')
        for j, subset in enumerate(subsets(dataset, catalog)):
            request = dataset_request(dataset, catalog, subset)
            selected = copy.deepcopy(subset)
            if task['accuracy']['evaluator'] == 'evalscope':
                selected.pop('max_prompt_tokens', None)
                if request.get('recipe') in ('ceval', 'gsm8k'):
                    request['layout'] = 'evalscope'
            jobs.append({'id': 'dataset-' + str(i) + '-' + str(j), 'dataset': dataset,
                         'catalog': catalog, 'subset': selected,
                         'native_data_files': native_files,
                         'request': request})
    return jobs


def native_catalog(task, dataset, catalog, observations):
    if not isinstance(task['model']['model_name'], str):
        raise ValueError('原生目录发现等待默认 model.model_name 解析完成')
    snapshot = sources.bound(catalog['source'])
    if snapshot.get('data_files'):
        catalog_files(dataset['path'], snapshot['data_files'])
    original = copy.deepcopy(snapshot['inputs']['dataset'])
    if type(original['limit']) is int and original['limit'] == -1:
        original['limit'] = None
    expected = {'model': task['model']['model_name'],
                'model_path': observations['model_resolution']['request']['path']}
    if original != dataset or any(snapshot['inputs'].get(key) != value for key, value in expected.items()):
        raise ValueError('原生目录发现不属于当前模型、数据或样本选择')
    if snapshot['evaluator_version'] != observations['evaluator']['version']:
        raise ValueError('原生目录发现使用了不同评测版本')
    names = [row['name'] for row in catalog['subsets']]
    if names != snapshot['subset_names']:
        raise ValueError('Agent 子集目录未完整保留原生工具的实际范围和顺序')
    discovery_splits(catalog, snapshot)
    return snapshot


def discovery_splits(catalog, snapshot):
    actual = {row['name']: row for row in snapshot['subsets']}
    for row in catalog['subsets']:
        split = actual.get(row['name'], {}).get('split', snapshot.get('eval_split'))
        if row['split'] != split:
            raise ValueError('原生子集 split 与发现结果不一致')


def catalog_files(path, expected):
    root = Path(path)
    base = root.parent if root.is_file() else root
    files = [root] if root.is_file() else sorted(root.rglob('*'))
    actual = {}
    for file in files:
        if file.is_symlink() or file.resolve() != file:
            raise ValueError('原生数据内容路径出现链接重定向')
        if file.is_file():
            actual[file.relative_to(base).as_posix()] = sources.ref(file)['sha256']
    if not actual or actual != expected:
        raise ValueError('原生数据内容与 token 长度发现时不同，须重新发现后续接')
