"""委托数据准备公开入口，按每个子集实际原文件取得全量选择清单。"""
from pathlib import Path
import sys
import uuid

from coordination.runtime import sources
from coordination.runtime.process import call
from skill_paths import skill_root
from ..state import identity
from .resolution import publish
from .snapshot import verify_upstream
from .upstream import REPOSITORIES


def material(value):
    return sources.bound(value) if isinstance(value, dict) and set(value) == {'path', 'sha256'} else value


def load_source(value, name):
    extra = material(value.get('quality_sources', {}).get(name, {}))
    base = value.get('datasets', {}).get(name, {})
    result = {**base, **extra}
    if name in REPOSITORIES:
        result.setdefault('repository', REPOSITORIES[name])
    return result


def catalog_info(source, dataset, evaluator, root):
    """普通 catalog 在构造时覆盖 few_shot_num=0；质量测量独立读取未覆盖的注册参数。"""
    ref = source.get('catalog')
    cached = root / 'native-catalog.json'
    if cached.exists():
        catalog = sources.bound(sources.read(cached))
    elif ref and material(ref).get('format') == 'evalscope-quality-catalog-v1':
        if set(ref) != {'path', 'sha256'}:
            raise ValueError('质量原生目录须绑定原始文件')
        catalog = sources.bound(ref)
    else:
        attempt = root / 'discovery' / uuid.uuid4().hex
        attempt.mkdir(parents=True)
        cmd = [evaluator['python'], '-B', str(Path(__file__).with_name('discover.py')), dataset]
        code, out = call(cmd, 120, attempt, 'native-metadata')
        if code:
            raise ValueError('缺少匹配版本的原生质量目录：' + str(attempt / 'native-metadata.stderr.txt'))
        catalog = sources.parse(out)
    if catalog['name'] != dataset or catalog['evaluator_version'] != evaluator['version']:
        raise ValueError('原生目录实际评测版本或 benchmark 不同')
    adapter = catalog['adapter_source']
    if sources.ref(adapter['path']) != adapter:
        raise ValueError('原生 adapter 源码身份发生变化')
    frozen = publish(root / 'catalogs' / (identity(catalog) + '.json'), catalog)
    publish(cached, frozen)
    return catalog


def run_prepare(request, root):
    path = root / 'requests' / (identity(request) + '.json')
    publish(path, request)
    index = root / 'preparations' / (identity(request) + '.json')
    if index.exists():
        ref = sources.read(index)
    else:
        attempt = root / 'work' / uuid.uuid4().hex
        attempt.mkdir(parents=True)
        cli = skill_root('vllm-dataset-prepare') / 'scripts/prepare.py'
        args = [sys.executable, '-B', str(cli), '--request', str(path),
                '--work-root', str(attempt), '--run-id', 'quality-data']
        code, stdout = call(args, 14400, attempt, 'prepare')
        if code:
            raise ValueError('质量数据准备失败，见 ' + str(attempt / 'prepare.stderr.txt') + ' ' + stdout[:512])
        receipt = Path(sources.parse(stdout)['manifest'])
        if not receipt.is_relative_to(attempt) or receipt.resolve() != receipt:
            raise ValueError('数据准备回执位置无效')
        ref = sources.ref(receipt)
        publish(index, ref)
    report = sources.bound(ref)
    if report.get('task_id') != 'vllm-dataset-prepare' or report.get('conclusions', {}).get('assets') != 'pass':
        raise ValueError('质量数据集准备缺少有效回执')
    return ref, report['asset']


def check_snapshot(binding_ref, chosen, prepared):
    binding = sources.bound(binding_ref)
    if (binding['revision'] != chosen['dataset_revision']
            or binding['repository'] != chosen['selection']['repository']):
        raise ValueError('本地数据内容清单未绑定本次官方题库快照')
    rows = prepared.get('provenance', {}).get('source_files', prepared['files'])
    data = {row['path']: row['sha256'] for row in rows if row.get('split') is not None}
    root = Path(prepared.get('provenance', {}).get('source_root', prepared['path']))
    for name, digest in data.items():
        actual_path = root / name
        if actual_path.resolve() != actual_path or not actual_path.is_relative_to(root) or sources.ref(actual_path)['sha256'] != digest:
            raise ValueError('本地题库在准备后变化：' + name)
    if not data or data != binding['files']:
        raise ValueError('原始题库文件与官方快照完整文件清单不同')


def source_file(source, subset, files, split, recipe):
    options = [r['path'] for r in files if r['split'] == split and r.get('subject') == subset]
    if len(options) == 1:
        return options[0]
    if recipe == 'gsm8k':
        return split + '.jsonl'
    options = [r['source_file'] for r in source.get('subsets', []) if r['name'] == subset and r['split'] == split]
    if len(options) != 1:
        raise ValueError('原生子集没有唯一对应的原文件：' + subset)
    return options[0]


def full_selection(asset, subset, file):
    matches = [r for r in asset['files'] if r['path'] == file]
    if len(matches) != 1:
        raise ValueError('未找到子集的正式原文件：' + subset)
    rows = asset['selection']['records']
    count = matches[0]['rows']
    if not count or [r['row_index'] for r in rows] != list(range(count)) or any(r['file'] != file for r in rows):
        raise ValueError('GPU 基线复测需覆盖该子集全部样本：' + subset)
    return count


def prepare_data(source, selected, catalog, subsets, root):
    split = catalog['eval_split']
    original = dict(source['preparation'])
    original.update(split=split, selection={}, subjects=[])
    recipe = original.get('recipe', 'generic')
    if recipe in ('ceval', 'gsm8k'):
        original['layout'] = 'evalscope'
    # Make a common native data root. Per-subset receipts then bind different
    # source-row selections within that same root.
    _, shared = run_prepare(original, root)
    check_snapshot(source['snapshot_binding'], selected, shared)
    verify_upstream(sources.bound(source['snapshot_binding']), source, root)
    local = {'source': 'local', 'path': shared['path'], 'recipe': recipe, 'split': split,
             'expected_identity': shared['identity']}
    for key in ('format', 'required_fields', 'csv_header', 'files', 'supporting_files'):
        if key in original:
            local[key] = original[key]
    if shared.get('layout') == 'evalscope':
        local['supporting_files'] = ['README.md']
    receipts, selections = {}, {}
    for subset in subsets:
        file = source_file(source, subset, shared['files'], split, recipe)
        ref, asset = run_prepare({**local, 'selection': {'file': file}}, root)
        count = full_selection(asset, subset, file)
        receipts[subset] = ref
        selections[subset] = {'count': count, 'sha256': asset['selection']['sha256']}
    return shared, receipts, selections
