"""用有限时长抓取公开资料，保留原始响应和真实来源。"""
import hashlib
from http.client import HTTPException
import json
from time import monotonic
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request

from coordination.runtime import sources
from ..resolution import publish

ERRORS = (*sources.ERRORS, HTTPException, UnicodeError, AttributeError)
MAX_BYTES = 16 * 1024**2
HOSTS = {'huggingface.co', 'github.com', 'api.github.com', 'raw.githubusercontent.com'}


def allowed(url, redirect=False):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443):
        return False
    host = parts.hostname or ''
    return host in HOSTS or (redirect and host.endswith(('.hf.co', '.huggingface.co', '.githubusercontent.com')))


class Redirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl, redirect=True):
            raise ValueError('公开资料重定向到未核验的来源')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, timeout):
    if not allowed(url):
        raise ValueError('只接受可识别的官方 HTTPS 发布地址')
    req = Request(url, headers={'User-Agent': 'vllm-model-migration'})
    end = monotonic() + timeout
    with build_opener(Redirect()).open(req, timeout=timeout) as response:
        raw = bytearray()
        while len(raw) <= MAX_BYTES:
            if monotonic() >= end:
                raise TimeoutError('公开资料下载超时')
            block = response.read(128 * 1024)
            if not block:
                return bytes(raw)
            raw.extend(block)
    raise ValueError('公开资料超过大小限制')


class Search:
    def __init__(self, root):
        self.root = root
        self.sources = {}
        self.missing = []
        self.ends = monotonic() + 25

    def get(self, url):
        if url not in self.sources:
            remaining = self.ends - monotonic()
            if remaining <= 0:
                raise TimeoutError('公开基线搜索超过本轮时间预算')
            try:
                raw = fetch(url, min(6, remaining))
                digest = hashlib.sha256(raw).hexdigest()
                payload = publish(self.root / 'public' / digest, raw)
                self.sources[url] = {'url': url, 'payload': payload}
            except ERRORS as error:
                self.sources[url] = {'url': url, 'error': type(error).__name__}
                raise ValueError('无法读取公开来源 ' + url + ' (' + type(error).__name__ + ')') from error
        row = self.sources[url]
        if 'payload' not in row:
            raise ValueError('此前未能读取公开来源 ' + url)
        return sources.stable_bytes(row['payload']['path'], MAX_BYTES)

    def json(self, url):
        return json.loads(self.get(url), object_pairs_hook=sources.pairs, parse_constant=sources.invalid_constant)
