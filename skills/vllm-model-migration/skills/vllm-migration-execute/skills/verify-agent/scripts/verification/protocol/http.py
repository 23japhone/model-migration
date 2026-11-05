"""不跟随重定向的有界HTTP，保存完整原始响应而不保存认证值。"""
import http.client
from contextlib import contextmanager
import signal
import threading
import json
import os
import time
from urllib.parse import urlsplit

from ..evidence import write_json
from .sse import parse

MAX_BYTES = 16 * 1024 * 1024


@contextmanager
def total_timeout(seconds):
    if threading.current_thread() is not threading.main_thread():
        raise ValueError('HTTP验证须在独立CLI主线程执行，确保总时限有效')
    previous = signal.getsignal(signal.SIGALRM)
    timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()
    def expired(signum, frame):raise TimeoutError('HTTP总时限超时')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        remaining = max(.000001, timer[0] - (time.monotonic() - started)) if timer[0] else 0
        signal.setitimer(signal.ITIMER_REAL, remaining, timer[1])


def read_lines(response, destination, timeout):
    total = 0
    deadline = time.monotonic() + timeout
    with destination.open('wb') as stream:
        while True:
            if time.monotonic() >= deadline:raise TimeoutError('响应总时限超时')
            raw = response.readline(MAX_BYTES + 1)
            if not raw:break
            total += len(raw)
            stream.write(raw)
            if total > MAX_BYTES:raise ValueError('响应超过证据大小限制')
            yield raw


def exchange(endpoint, route, payload, attempt, timeout, model=None, api_key_env=None):
    url = urlsplit(endpoint)
    connection_type = http.client.HTTPSConnection if url.scheme == 'https' else http.client.HTTPConnection
    connection = connection_type(url.hostname, url.port, timeout=min(timeout, 30))
    headers = {'Content-Type': 'application/json'}
    if api_key_env:headers['Authorization'] = 'Bearer ' + os.environ[api_key_env]
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    if payload is not None:write_json(attempt / 'request.json', payload)
    started = time.monotonic()
    try:
        with total_timeout(timeout):
            connection.request('GET' if payload is None else 'POST', route, body=body, headers=headers)
            # 生成可能比连接慢，读取使用显式且有界的用例超时。
            if connection.sock:connection.sock.settimeout(timeout)
            response = connection.getresponse()
            write_json(attempt / 'http.json', {'status': response.status,
                                             'content_type': response.getheader('Content-Type')})
            streaming = bool(payload and payload.get('stream'))
            path = attempt / ('response.sse' if streaming else 'response.json')
            lines = read_lines(response, path, timeout)
            if response.status != 200:
                list(lines)
                raise ValueError('HTTP 状态不是 200')
            if streaming:
                if 'text/event-stream' not in (response.getheader('Content-Type') or ''):
                    list(lines)
                    raise ValueError('SSE Content-Type 不正确')
                result = parse(lines, model)
                write_json(attempt / 'assembled.json', result)
            else:
                raw = b''.join(lines)
                result = json.loads(raw)
            return result, time.monotonic() - started
    finally:
        connection.close()
