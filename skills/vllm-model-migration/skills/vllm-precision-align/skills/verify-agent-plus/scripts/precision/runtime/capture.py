"""本次自有回环记录器，逐字节透传请求并保存真实 HTTP 结果。"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import socket
import threading
from urllib.parse import urlsplit
import uuid

from ..evidence import sha256, write, now
from ..http_evidence import validate_response

LIMIT=64*1024*1024


def request_started(folder, method, route):
    if method == 'POST' and route in ('/v1/chat/completions', '/v1/completions'):
        write(folder / 'progress.json', {'phase': 'evaluating', 'route': route, 'started_at': now()})


def close_socket(connection):
    try:connection.shutdown(socket.SHUT_RDWR)
    except OSError:pass
    connection.close()


def handler(endpoint,folder,index,lock,timeout,connections,validator=validate_response):
    upstream=urlsplit(endpoint)
    def register(connection):
        with lock:
            if connections['closing']:
                close_socket(connection)
                raise OSError('本次 HTTP 记录已结束')
            connections['active'].add(connection)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass

        def setup(self):
            super().setup()
            self.connection.settimeout(timeout)
            register(self.connection)

        def finish(self):
            try:super().finish()
            finally:
                with lock:connections['active'].discard(self.connection)

        def do_GET(self):self.forward()

        def do_POST(self):self.forward()

        def forward(self):
            record={'method':self.command,'route':self.path,'files':{}}
            local=folder/uuid.uuid4().hex;local.mkdir()
            connection=None;upstream_socket=None
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length<0 or length>LIMIT:raise ValueError('请求正文超过记录边界')
                body=self.rfile.read(length)
                (local/'request.bin').write_bytes(body)
                headers={key:value for key,value in self.headers.items()
                         if key.lower() in ('content-type','accept','user-agent')}
                connection=http.client.HTTPConnection(upstream.hostname,upstream.port,timeout=timeout)
                connection.connect();upstream_socket=connection.sock
                register(upstream_socket)
                connection.request(self.command,self.path,body=body,headers=headers)
                request_started(folder,self.command,self.path)
                response=connection.getresponse();raw=response.read(LIMIT+1)
                if len(raw)>LIMIT:raise ValueError('响应正文超过记录边界')
                (local/'response.bin').write_bytes(raw)
                record.update(status=response.status,errors=validator(raw,response.status) if self.command=='POST' else [])
                self.send_response(response.status)
                self.send_header('Content-Type',response.getheader('Content-Type','application/json'))
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            except (OSError,ValueError,http.client.HTTPException) as error:
                record['errors']=['HTTP 透传失败：'+str(error)]
                try:self.send_error(502,'inference capture failed')
                except OSError:pass
            finally:
                if connection is not None:connection.close()
                with lock:connections['active'].discard(upstream_socket)
                for path in local.iterdir():record['files'][str(path.relative_to(folder))]=sha256(path)
                with lock:
                    index['requests'].append(record);index['errors'].extend(record.get('errors',[]))
    return Handler


@contextmanager
def recording(endpoint,folder,timeout,validator=validate_response):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=False)
    index={'requests':[],'errors':[]};lock=threading.Lock()
    connections={'active':set(),'closing':False}
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(endpoint,folder,index,lock,timeout,connections,validator))
    server.daemon_threads=False
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:yield 'http://127.0.0.1:'+str(server.server_port)
    finally:
        server.shutdown()
        with lock:
            connections['closing']=True
            active=list(connections['active'])
        for connection in active:close_socket(connection)
        server.server_close();worker.join(timeout=2)
        write(folder/'index.json',index)
