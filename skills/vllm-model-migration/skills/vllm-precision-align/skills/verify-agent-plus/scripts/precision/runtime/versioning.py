"""运行时核验官方最新稳定版并保存回执；不安装依赖，不参与历史分析。"""
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
from pathlib import Path
import sys
from urllib.error import URLError
from urllib.request import urlopen

PACKAGES = {'evalscope': 'evalscope', 'aisbench': 'ais-bench-benchmark'}


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def latest_stable(package, attempt, receipt):
    from packaging.version import InvalidVersion, Version
    url = 'https://pypi.org/pypi/' + package + '/json'
    receipt['index_url'] = url
    try:
        with urlopen(url, timeout=15) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError('发布索引超出大小边界')
        path = attempt / 'evaluator-index.json'; path.write_bytes(raw)
        receipt['index_sha256'] = hashlib.sha256(raw).hexdigest()
        value = json.loads(raw)
        if value['info']['name'].lower().replace('_', '-') != package:
            raise ValueError('发布索引项目身份不符')
        candidates = []
        for label, files in value['releases'].items():
            try:
                version = Version(label)
            except InvalidVersion:
                continue
            if not version.is_prerelease and not version.is_devrelease and any(not row['yanked'] for row in files):
                candidates.append((version, label))
        if not candidates:
            raise ValueError('没有未撤回的稳定发布')
        return max(candidates)[1]
    except (OSError, URLError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError('无法核验官方最新稳定版：' + str(exc)) from exc


def verify_installation(distribution, evaluator, attempt, receipt):
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
    installed = distribution.version
    requirement = distribution.metadata.get('Requires-Python', '')
    receipt.update(installed_version=installed, requires_python=requirement, python=sys.version)
    if requirement and not SpecifierSet(requirement).contains('.'.join(map(str, sys.version_info[:3])), prereleases=True):
        raise ValueError('评测解释器不符合实际发行包 Requires-Python=' + requirement)
    if receipt['policy'] == 'latest':
        expected = latest_stable(receipt['package'], attempt, receipt)
        receipt['latest_version'] = expected
        if Version(installed) != Version(expected):
            raise ValueError('需要官方最新稳定版 ' + expected + '，实际为 ' + installed +
                             '；请在独立评测环境执行 python -m pip install --upgrade ' + receipt['package'])
    else:
        receipt['expected_version'] = evaluator['version']
        if Version(installed) != Version(evaluator['version']):
            raise ValueError('实际安装版本与旧请求的期望版本不同；新任务可省略 version 使用最新稳定版')


def inspect_environment(backend, evaluator, attempt):
    attempt = Path(attempt)
    receipt = {'backend': backend, 'package': PACKAGES[backend],
               'policy': 'latest' if evaluator.get('version', 'latest') == 'latest' else 'legacy_exact',
               'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'blocked'}
    try:
        distribution = metadata.distribution(receipt['package'])
        verify_installation(distribution, evaluator, attempt, receipt)
        receipt['status'] = 'pass'
    except (ValueError, ImportError, metadata.PackageNotFoundError) as exc:
        receipt['error'] = str(exc)
        raise ValueError('评测环境版本核验失败：' + str(exc)) from exc
    finally:
        save(attempt / 'evaluator-version.json', receipt)
    return receipt
