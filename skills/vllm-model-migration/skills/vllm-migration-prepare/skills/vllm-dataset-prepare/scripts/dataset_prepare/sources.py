"""HTTPS 资产下载、摘要复核与已准备内容复用。"""
import json
from pathlib import Path
import tarfile
from urllib.parse import urlsplit
import urllib.request
import uuid
import zipfile

from .archive import unpack
from .assets import checked_files,file_hash
from .results import write_json


class HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    """在发出下一跳请求前拒绝协议降级。"""
    def redirect_request(self,request,response,code,message,headers,url):
        if urlsplit(url).scheme!='https':raise ValueError('数据集重定向只允许 HTTPS，不发送降级请求')
        return super().redirect_request(request,response,code,message,headers,url)


def open_source(url,timeout):
    """保留标准 TLS 与代理处理，增加逐跳协议核验。"""
    return urllib.request.build_opener(HTTPSRedirectHandler()).open(url,timeout=timeout)


def verify_download(path,request,record):
    if record.get('url')!=request['url']:raise ValueError('已有下载来源不符，不覆盖资产')
    if record.get('revision')!=request.get('revision'):raise ValueError('已有来源 revision 说明不符，不覆盖资产')
    actual=file_hash(path)
    if actual!=record.get('sha256') or path.stat().st_size!=record.get('size'):raise ValueError('已有下载缺失或损坏，不覆盖资产')
    if request.get('expected_sha256') and request['expected_sha256']!=actual:raise ValueError('下载摘要与预期不一致')


def extracted_identity(path):
    root=Path(path);base=root if root.is_dir() else root.parent
    return {p.relative_to(base).as_posix():{'size':p.stat().st_size,'sha256':file_hash(p)} for p in checked_files(root)}


def verify_prepared(root,record):
    target=Path(record['dataset_path'])
    if target.resolve()!=target or not target.is_relative_to(root):raise ValueError('已解包目录不属于本次来源资产目录')
    if not target.exists():raise ValueError('已解包数据缺失，保留原始下载与记录')
    if extracted_identity(target)!=record.get('extracted_identity'):raise ValueError('已解包数据损坏或变化，不覆盖资产')
    return str(target)


def download(request,root,on_snapshot):
    source=root/'source-record.json';payload=root/'source-payload'
    if source.is_symlink() or payload.is_symlink():raise ValueError('下载文件与来源记录不能是符号链接')
    if payload.exists():
        if not source.is_file():raise ValueError('已有文件没有来源记录，不覆盖资产')
        record=json.loads(source.read_text());verify_download(payload,request,record);return record,'reused'
    if source.exists():raise ValueError('已有来源记录的下载文件缺失，不覆盖旧记录')
    temporary=root/(uuid.uuid4().hex+'.part')
    record={'kind':'https','url':request['url'],'revision':request.get('revision'),'version_status':'declared_or_unversioned','partial_path':str(temporary)}
    on_snapshot(record)
    record.update(transfer(request,temporary))
    if request.get('expected_sha256') and record['sha256']!=request['expected_sha256']:raise ValueError('下载 SHA256 与预期不一致，部分文件已保留')
    temporary.rename(payload);write_json(source,record);on_snapshot(record)
    return record,'downloaded'


def transfer(request,temporary):
    with open_source(request['url'],timeout=request['timeout_seconds']) as response,temporary.open('xb') as output:
        if urlsplit(response.url).scheme!='https':raise ValueError('下载重定向不能降级为 HTTP')
        length=response.headers.get('Content-Length');total=0
        if length and int(length)>request['max_bytes']:raise ValueError('下载大小超过 max_bytes')
        while True:
            block=response.read(1024*1024)
            if not block:break
            total+=len(block)
            if total>request['max_bytes']:raise ValueError('下载大小超过 max_bytes，部分文件已保留')
            output.write(block)
        if length and total!=int(length):raise ValueError('下载内容不完整，部分文件已保留')
        output.flush()
        return {'size':total,'etag':response.headers.get('ETag'),'sha256':file_hash(temporary)}


def prepare_source(request,on_snapshot):
    if request['source']=='local':return request['path'],{'kind':'local','upstream_status':'unverified'},'reused'
    root=Path(request['path']);root.mkdir(parents=True,exist_ok=True)
    if root.is_symlink():raise ValueError('资产目录不能是符号链接')
    record,operation=download(request,root,on_snapshot)
    payload=root/'source-payload';source=root/'source-record.json'
    if record.get('dataset_path'):
        return verify_prepared(root,record),record,operation
    if zipfile.is_zipfile(payload) or tarfile.is_tarfile(payload):
        target=root/('extracted-'+uuid.uuid4().hex);unpack(payload,target,request['max_bytes'])
    else:target=payload
    record['dataset_path']=str(target);record['extracted_identity']=extracted_identity(target)
    write_json(source,record);on_snapshot(record)
    return str(target),record,operation
