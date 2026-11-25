"""由客户模型解析发布者，固定模型卡以及其明确链接的资料仓库。"""
from collections import deque
from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from urllib.parse import quote, unquote, urlencode, urlsplit

from .transport import ERRORS

SLUG = r'[A-Za-z0-9][A-Za-z0-9._-]*'
REPO = SLUG + '/' + SLUG
SHA = r'[0-9a-f]{40}|[0-9a-f]{64}'
LINK = re.compile(r'https://[^\s<>\x22\x27)\]]+')
LOCAL_LINK = re.compile(r'\]\(<?([^\s<>)]*\.json)(?:[?#][^\s<>)]*)?>?(?:\s+[^)]*)?\)', re.I)


def safe_path(name):
    path = PurePosixPath(name)
    if (not path.parts or path.is_absolute() or path.as_posix() != name or '..' in path.parts
            or '\\' in name or any(ord(c) < 32 for c in name)):
        raise ValueError('评测附件在资料仓库之外')
    return quote(name, safe='/')


@dataclass
class Published:
    prefix: str
    files: tuple

    def url(self, file):
        return self.prefix + safe_path(file)


def model_ids(search, model):
    repo = model.get('model_repository_id')
    if isinstance(repo, str) and re.fullmatch(REPO, repo):
        return [repo]
    name = model.get('model_name')
    if not isinstance(name, str) or not name.strip():
        raise ValueError('缺少可检索的模型名称')
    if re.fullmatch(REPO, name):
        return [name]
    results = search.json('https://huggingface.co/api/models?' + urlencode({'search': name, 'limit': 100}))
    ids = {r['id'] for r in results if r['id'].split('/')[-1].casefold() == name.casefold() and re.fullmatch(REPO, r['id'])}
    if not ids:
        raise ValueError('公开索引未找到与模型名相同的发布仓库')
    if len(results) >= 100:
        search.missing.append('本轮仅检查公开索引前 100 项中的同名候选')
    return sorted(ids)


def model_card(search, repo):
    info = search.json('https://huggingface.co/api/models/' + repo)
    if info.get('id') != repo or not re.fullmatch(SHA, info.get('sha', '')):
        raise ValueError('模型仓库或不可变版本未得到官方索引确认')
    url = 'https://huggingface.co/' + repo
    tree = Published(url + '/resolve/' + info['sha'] + '/', tuple(r['rfilename'] for r in info['siblings']))
    readme = tree.url('README.md')
    try:
        card = search.get(readme).decode('utf-8-sig')
    except ERRORS as error:
        card = ''
        search.missing.append(str(error))
    origin = {'repository_id': repo, 'revision': info['sha'], 'repository_url': url,
              'model_card': search.sources.get(readme)}
    return tree, origin, card


def pointer(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.netloc not in ('github.com', 'raw.githubusercontent.com', 'huggingface.co'):
        return None
    seg = unquote(parts.path).strip('/').split('/')
    is_hf = parts.netloc == 'huggingface.co'
    kind = 'model' if is_hf else 'github'
    if is_hf and seg[0] in ('datasets', 'spaces'):
        kind = seg.pop(0)
    if len(seg) < 2 or not re.fullmatch(REPO, '/'.join(seg[:2])):
        return None
    repo = '/'.join(seg[:2])
    tail = seg[2:]
    if parts.netloc == 'raw.githubusercontent.com':
        tail = ['raw', *tail]
    if len(tail) >= 3 and tail[0] in ('blob', 'resolve', 'raw'):
        return kind, repo, tail[1], '/'.join(tail[2:])
    if not tail:
        return kind, repo, '', ''
    return None


def assessment_file(path):
    name = path.casefold()
    if not name.endswith('.json') or any(x in name for x in ('safetensors', 'tokenizer', 'vocab')):
        return False
    return any(x in name for x in ('eval', 'benchmark', 'assessment', 'report', 'history', 'baseline', 'results/'))


def external(search, kind, repo, tag):
    if kind == 'github':
        api = 'https://api.github.com/repos/' + repo
        if not tag:
            tag = search.json(api)['default_branch']
        revision = search.json(api + '/commits/' + quote(tag, safe=''))['sha']
        if not re.fullmatch(SHA, revision):
            raise ValueError('GitHub 附件缺少固定提交')
        index = search.json(api + '/git/trees/' + revision + '?recursive=1')
        files = tuple(r['path'] for r in index['tree'] if r['type'] == 'blob')
        return Published('https://raw.githubusercontent.com/' + repo + '/' + revision + '/', files)
    api_type = {'datasets': 'datasets', 'model': 'models'}.get(kind)
    if not api_type:
        raise ValueError('未支持的模型卡附件来源')
    url = 'https://huggingface.co/api/' + api_type + '/' + repo
    info = search.json(url + ('/revision/' + quote(tag, safe='') if tag else ''))
    if info['id'] != repo or not re.fullmatch(SHA, info.get('sha', '')):
        raise ValueError('公开评测附件仓身份或版本不匹配')
    prefix = ('datasets/' if kind == 'datasets' else '') + repo
    return Published('https://huggingface.co/' + prefix + '/resolve/' + info['sha'] + '/',
                     tuple(r['rfilename'] for r in info['siblings']))


def linked_targets(origin, card, extra):
    links = [p for url in LINK.findall(card) if (p := pointer(url))]
    linked = set(links)
    # 只有明确列出的评测附件或评测仓库能将模型卡的发布关系延伸到其他来源。
    roots = {(kind, repo) for kind, repo, _, file in links if not file and evaluation_repo(repo, origin)}
    roots.add(('model', origin['repository_id']))
    provided = [p for url in extra if (p := pointer(url))]
    for kind, repo, tag, file in [*provided, *links]:
        if (kind, repo, tag, file) not in linked and (kind, repo) not in roots:
            continue
        if file and not file.lower().endswith('.json'):
            continue
        if not file and (kind, repo) not in roots:
            continue
        yield kind, repo, tag, file


def evaluation_repo(repo, origin):
    name = repo.split('/')[-1].lower()
    return any(word in name for word in ('eval', 'bench', 'baseline', origin['repository_id'].split('/')[-1].lower()))


def model_files(tree, card):
    linked = {PurePosixPath(unquote(path)).as_posix() for path in LOCAL_LINK.findall(card)}
    attached = {path for path in tree.files if assessment_file(path) or path in linked}
    return sorted(attached, key=priority)


def candidates(search, tree, origin, card, extra):
    seen = set()
    for file in model_files(tree, card):
        seen.add(tree.url(file))
        yield tree, file
    fetched = {}
    own = ('model', origin['repository_id'])
    for kind, repo, tag, file in linked_targets(origin, card, extra):
        try:
            key = kind, repo, tag
            if key not in fetched:
                fetched[key] = tree if (kind, repo) == own else external(search, *key)
            published = fetched[key]
            files = (file,) if file else sorted(filter(assessment_file, published.files), key=priority)
            for member in files:
                if member in published.files and published.url(member) not in seen:
                    seen.add(published.url(member))
                    yield published, member
        except ERRORS as error:
            search.missing.append(str(error))


def priority(path):
    name = PurePosixPath(path).name.lower()
    return (not any(w in name for w in ('history', 'index', 'baseline', 'manifest')), path)


def published_files(search, model, extra):
    queue = deque()
    for repo in model_ids(search, model):
        try:
            tree, origin, card = model_card(search, repo)
            queue.append((origin, candidates(search, tree, origin, card, extra)))
        except ERRORS as error:
            search.missing.append(repo + '：' + str(error))
    # 全部仓库共享同一检索预算。轮流取附件，避免一个仓库消耗整轮配额。
    while queue:
        origin, items = queue.popleft()
        try:
            tree, file = next(items)
        except StopIteration:
            continue
        except ERRORS as error:
            search.missing.append(origin['repository_id'] + '：' + str(error))
            continue
        queue.append((origin, items))
        yield origin, tree, file
