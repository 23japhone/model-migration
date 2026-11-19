"""有界、可对账的 HTTP；不自动重试、不跟随重定向、限制总请求次数。"""
import http.client
import json
from urllib.parse import urlsplit

MAX_BYTES = 16 * 1024 * 1024


class RequestBudgetExceeded(Exception):
    """定界请求次数超过契约预算。"""


class Client:
    """每次定界会话的 HTTP 客户端；记录每次请求台账供对账。"""

    def __init__(self, endpoint, timeout, budget):
        url = urlsplit(endpoint)
        if url.scheme not in ('http', 'https'):
            raise ValueError('服务地址仅支持 http/https')
        if not url.hostname or url.port is None:
            raise ValueError('服务地址缺少主机或端口')
        self._scheme = url.scheme
        self._host = url.hostname
        self._port = url.port
        self._timeout = timeout
        self._budget = budget
        self._count = 0
        self._ledger = []

    @property
    def request_count(self):
        return self._count

    @property
    def ledger(self):
        return list(self._ledger)

    def request(self, route, payload=None):
        """发送 GET(无 payload) 或 POST JSON；返回 (ok, data)。data 为解析后的
        JSON 对象或错误字符串。失败不重试。"""
        if self._count >= self._budget:
            raise RequestBudgetExceeded('定界请求超过预算 %d 次' % self._budget)
        self._count += 1
        connection_type = http.client.HTTPSConnection if self._scheme == 'https' \
            else http.client.HTTPConnection
        connection = connection_type(self._host, self._port, timeout=self._timeout)
        method = 'POST' if payload is not None else 'GET'
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type': 'application/json'} if payload is not None else {}
        record = {'method': method, 'route': route, 'payload': payload}
        try:
            connection.request(method, route, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError('响应超过证据大小限制')
            record['status'] = response.status
            self._ledger.append(record)
            if response.status != 200:
                return False, 'HTTP %s: %s' % (response.status,
                                              raw.decode('utf-8', errors='ignore')[:300])
            return True, json.loads(raw.decode('utf-8'))
        except (OSError, ValueError, http.client.HTTPException) as error:
            record['error'] = str(error)
            self._ledger.append(record)
            return False, str(error)
        finally:
            connection.close()
