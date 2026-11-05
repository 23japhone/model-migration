"""完整源码树身份、允许范围和静态编译；不执行或改写目标源码。"""
import difflib
import os
from pathlib import Path
import stat

from .evidence import canonical, stable

EXCLUDED = {'.git', '__pycache__', '.pytest_cache'}


def unreadable(error):
    raise error


def inventory(root):
    root = Path(root)
    if root.resolve() != root or not root.is_dir():raise ValueError('源码目录不存在或为符号链接')
    result = {}
    for base, directories, files in os.walk(root, onerror=unreadable):
        directories[:] = sorted(d for d in directories if d not in EXCLUDED)
        for name in [*directories, *sorted(files)]:
            path = Path(base) / name
            if path.is_symlink():raise ValueError('源码含符号链接：' + str(path))
        for name in sorted(files):
            path = Path(base) / name
            if not stat.S_ISREG(path.stat().st_mode):raise ValueError('源码只允许普通文件')
            _, digest = stable(path)
            result[str(path.relative_to(root))] = {'sha256': digest, 'mode': stat.S_IMODE(path.stat().st_mode)}
    if not result:raise ValueError('源码树不能为空')
    return {'files': result, 'fingerprint': canonical(result)}


def changed(baseline, current):
    before, after = baseline['files'], current['files']
    result = []
    for path in sorted(before.keys() | after.keys()):
        if before.get(path) == after.get(path):continue
        kind = 'modified' if path in before and path in after else ('added' if path in after else 'deleted')
        result.append({'path': path, 'kind': kind, 'before': before.get(path), 'after': after.get(path)})
    return result


def in_scope(path, allow):
    return any(path == entry or path.startswith(entry + '/') for entry in allow)


def diff_text(source, changes):
    parts = []
    for item in changes:
        name = item['path']
        texts = []
        for key in ['baseline', 'working']:
            path = Path(source[key]) / name
            raw = stable(path, content=True)[0] if path.exists() else b''
            try:texts.append(raw.decode('utf-8').splitlines(keepends=True))
            except UnicodeDecodeError:
                texts = [];break
        if texts:
            for line in difflib.unified_diff(*texts, fromfile='a/' + name, tofile='b/' + name):
                parts.append(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n')
        else:parts.append('二进制差异：' + name + '\n')
    return ''.join(parts)


def inspect(name, source, baseline, attempt):
    errors = []
    observed = inventory(source['baseline'])
    if observed != baseline:errors.append('冻结后基线发生变化')
    current = inventory(source['working'])
    changes = changed(baseline, current)
    for item in changes:
        path = item['path']
        if not in_scope(path, source['allow']):errors.append('超出允许改动范围：' + path)
        if item['after'] and path.endswith('.py'):
            raw = stable(Path(source['working']) / path, content=True)[0]
            try:compile(raw, path, 'exec', dont_inherit=True)
            except (SyntaxError, ValueError) as error:errors.append('Python 编译失败：' + str(error))
    patch = attempt / (name + '.diff')
    patch.write_text(diff_text(source, changes), encoding='utf-8')
    # diff/编译读取后复核，避免将变化中的源码交接。
    if inventory(source['working']) != current or inventory(source['baseline']) != observed:
        errors.append('差异采集期间源码发生变化')
    return {'fingerprint': current['fingerprint'], 'files': current['files'], 'changes': changes,
            'diff': str(patch), 'diff_sha256': stable(patch)[1], 'errors': errors}
