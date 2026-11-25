"""从官方发布索引固定工具和题库版本，续接只读取本次已发布的选择。"""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import quote
from urllib.request import Request, urlopen

from coordination.runtime import sources
from ..state import identity


def publish(path, value):
    """先完整写入临时文件，再以不覆盖方式发布。"""
    path = Path(path)
    if path.resolve() != path:
        raise ValueError('冻结路径包含链接重定向')
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = value if isinstance(value, bytes) else (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
    with tempfile.NamedTemporaryFile(dir=path.parent) as tmp:
        tmp.write(raw)
        tmp.flush()
        try:
            os.link(tmp.name, path)
        except FileExistsError:
            if sources.stable_bytes(path) != raw:
                raise ValueError('冻结输入在并发准备或续接时变化：' + path.name)
    return sources.ref(path)


def source_json(url, config, root):
    if 'responses' in config:
        source = config['responses'].get(url)
        if not source:
            raise ValueError('缺少该官方 URL 的本地原始快照：' + url)
        snapshot = sources.bound(source)
        if snapshot['url'] != url:
            raise ValueError('本地索引快照的官方来源不一致')
    else:
        req = Request(url, headers={'User-Agent': 'vllm-model-migration', 'Accept': 'application/json'})
        with urlopen(req, timeout=15) as stream:
            raw = stream.read(16 * 1024**2 + 1)
        if len(raw) > 16 * 1024**2:
            raise ValueError('官方索引超过大小限制')
        snapshot = {'url': url, 'body': json.loads(raw)}
    ref = publish(root / 'indexes' / (identity(snapshot) + '.json'), snapshot)
    return snapshot['body'], ref


def version_key(label):
    match = re.fullmatch(r'v?(\d+(?:\.\d+)*)(?:[.-]?post(\d+))?', label, re.I)
    if not match:
        return None
    parts = tuple(int(i) for i in match[1].split('.'))
    while parts and not parts[-1]:
        parts = parts[:-1]
    return parts, int(match[2]) if match[2] else -1


def latest_tool(config, root, evidence):
    index, ref = source_json('https://pypi.org/pypi/evalscope/json', config, root)
    evidence.append(ref)
    if index['info']['name'].lower() != 'evalscope':
        raise ValueError('PyPI 索引包名不匹配')
    choices = [(version_key(label), label) for label, files in index['releases'].items()
               if version_key(label) is not None and any(f.get('yanked') is False for f in files)]
    if not choices:
        raise ValueError('PyPI 缺少可用的 EvalScope 稳定发行')
    return max(choices)[1]


def hf_revision(repo, read):
    index = read('https://huggingface.co/api/datasets/' + repo + '/refs')
    tags = [(version_key(row['name']), row) for row in index['tags'] if version_key(row['name']) is not None]
    if tags:
        row = max(tags, key=lambda item: item[0])[1]
        return row['targetCommit'], row['name']
    revision = read('https://huggingface.co/api/datasets/' + repo)['sha']
    branches = [row['name'] for row in index['branches'] if row['targetCommit'] == revision]
    return revision, branches[0] if len(branches) == 1 else 'default'


def github_revision(repo, read):
    base = 'https://api.github.com/repos/' + repo
    page = 1
    while True:
        rows = read(base + '/releases?per_page=100&page=' + str(page))
        stable = [row for row in rows if not row['draft'] and not row['prerelease']]
        if stable:
            tag = max(stable, key=lambda r: r['published_at'])['tag_name']
            return read(base + '/commits/' + quote(tag, safe=''))['sha'], tag
        if len(rows) < 100:
            break
        page += 1
    branch = read(base)['default_branch']
    return read(base + '/commits/' + quote(branch, safe=''))['sha'], branch


def modelscope_revision(repository, config, root, evidence):
    url = repository + '.git'
    if 'responses' in config:
        text, ref = source_json(url, config, root)
    else:
        env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
        query = ['git', '-c', 'credential.helper=', 'ls-remote', '--symref', url, 'HEAD', 'refs/tags/*']
        try:
            result = subprocess.run(query, env=env, capture_output=True, text=True, timeout=20, check=False)
        except subprocess.TimeoutExpired as error:
            raise ValueError('ModelScope 发行引用读取超时') from error
        if result.returncode:
            raise ValueError('ModelScope 发行引用读取失败：' + repository)
        text = result.stdout
        snap = {'url': url, 'body': text}
        ref = publish(root / 'indexes' / (identity(snap) + '.json'), snap)
    evidence.append(ref)
    entries = {name: val for val, name in (line.split('\t', 1) for line in text.splitlines())}
    tags = [name for name in entries if name.startswith('refs/tags/') and version_key(name[10:]) is not None]
    if tags:
        name = max(tags, key=lambda n: version_key(n[10:]))
        return entries.get(name + '^{}', entries[name]), name[10:]
    return entries['HEAD'], 'HEAD'


def repository_location(repository):
    match = re.fullmatch(r'https://(huggingface\.co/datasets|github\.com|www\.modelscope\.cn/datasets)/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)', repository)
    if not match or '..' in match[2]:
        raise ValueError('需提供已核验的 Hugging Face、ModelScope 数据集或 GitHub 仓库')
    return match


def dataset_revision(repository, config, root, evidence):
    match = repository_location(repository)
    def read(url):
        body, ref = source_json(url, config, root)
        evidence.append(ref)
        return body
    decide = hf_revision if match[1].startswith('huggingface') else github_revision
    if match[1].startswith('www.modelscope'):
        revision, label = modelscope_revision(repository, config, root, evidence)
    else:
        revision, label = decide(match[2], read)
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', revision):
        raise ValueError('官方索引没有返回不可变 commit')
    return revision, label


def resolve(baseline, config, root, *, pinned=None):
    root = Path(root)
    selection = {key: baseline.get(key) for key in ('dataset', 'evaluator', 'evaluator_version')}
    selection['repository'] = config['repository']
    if pinned:
        repository_location(config['repository'])
        revision = pinned['dataset_revision']
        if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', revision):
            raise ValueError('GPU 历史题库未固定到不可变 commit')
        selection['public_gpu'] = pinned
    path = root / 'release-selection.json'
    if path.exists():
        value = sources.read(path)
        if value['selection'] != selection:
            raise ValueError('已冻结的评测工具或数据集选择已改变')
        if pinned and value['dataset_revision'] != pinned['dataset_revision']:
            raise ValueError('已冻结的题库提交与 GPU 原始运行不同')
        for ref in value['evidence']:
            sources.bound(ref)
        return value
    evidence = []
    version = baseline.get('evaluator_version') or latest_tool(config, root, evidence)
    if pinned:
        revision = label = pinned['dataset_revision']
        evidence.append(pinned['gpu_evidence'])
    else:
        revision, label = dataset_revision(config['repository'], config, root, evidence)
    result = {'selection': selection, 'evaluator_version': version, 'dataset_revision': revision,
              'dataset_ref': label, 'evidence': evidence}
    publish(path, result)
    return result
