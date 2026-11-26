"""mock vLLM 服务：模拟一个返回「工具调用异常」的推理服务，供端到端测试。

行为设计成让 three-step-demarcation 得到 TOOL_PARSER：
- /version 返回 0.30.0（render 端点因未开 --enable-scale-out 而 404，降级 /tokenize）；
- /detokenize 返回含消息内容与工具函数名的 prompt（渲染检查全 PASS）；
- /v1/completions 返回带 <tool_call> 的原始文本（模型原始输出有工具调用）；
- /v1/chat/completions 返回无 tool_calls 的 message（chat 侧工具调用缺失）。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/version':
            self._send(200, {'version': '0.30.0'})
        elif self.path == '/v1/models':
            self._send(200, {'object': 'list', 'data': [{'id': 'Qwen3.5-27B'}]})
        else:
            self._send(404, {'detail': 'Not Found'})

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        if length:
            self.rfile.read(length)
        if self.path == '/tokenize':
            self._send(200, {'tokens': [1, 2, 3, 4, 5]})
        elif self.path == '/detokenize':
            self._send(200, {'prompt': '用户：北京今天天气怎么样 工具：get_weather'})
        elif self.path == '/v1/chat/completions/render':
            self._send(404, {'detail': 'Not Found'})
        elif self.path == '/v1/completions':
            self._send(200, {'choices': [{
                'text': '<tool_call>\n<function=get_weather>\n<parameter=city>\n北京\n</parameter>\n</function>\n</tool_call>',
                'finish_reason': 'stop'}]})
        elif self.path == '/v1/chat/completions':
            self._send(200, {'choices': [{
                'message': {'content': '好的，我帮你查。', 'tool_calls': None, 'reasoning_content': None},
                'finish_reason': 'stop'}]})
        else:
            self._send(404, {'detail': 'Not Found'})


class MockVLLM:
    def __init__(self):
        self._server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def endpoint(self):
        return 'http://127.0.0.1:%d' % self.port

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._server.shutdown()
        self._server.server_close()
