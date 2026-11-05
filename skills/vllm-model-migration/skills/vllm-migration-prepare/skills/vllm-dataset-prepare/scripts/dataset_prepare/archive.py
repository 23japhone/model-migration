"""预检归档所有成员，再在独立目录保留原布局解包。"""
from pathlib import Path,PurePosixPath
import shutil
import stat
import tarfile
import zipfile


def safe_name(name):
    if not isinstance(name,str) or '\\' in name or '\0' in name:raise ValueError('归档成员路径无效')
    path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:raise ValueError('归档成员不能越过目标目录')
    if path.as_posix()=='.':return None
    return path.as_posix()


def check_roster(rows,max_bytes):
    known={};total=0
    for name,is_dir,size,entry in rows:
        if name is None:continue
        if name in known:raise ValueError('归档包含重复路径')
        known[name]=is_dir;total+=size
        if total>max_bytes:raise ValueError('归档解包大小超过 max_bytes')
    for name in known:
        for parent in PurePosixPath(name).parents:
            if parent.as_posix() in known and not known[parent.as_posix()]:raise ValueError('归档文件与目录布局冲突')


def zip_rows(archive):
    rows=[]
    for item in archive.infolist():
        mode=item.external_attr>>16;kind=stat.S_IFMT(mode)
        if kind not in (0,stat.S_IFREG,stat.S_IFDIR):raise ValueError('ZIP 不能包含链接或特殊成员')
        rows.append((safe_name(item.filename),item.is_dir(),item.file_size,item))
    return rows


def tar_rows(archive):
    rows=[]
    for item in archive:
        if not (item.isfile() or item.isdir()):raise ValueError('tar 不能包含链接或特殊成员')
        rows.append((safe_name(item.name),item.isdir(),item.size,item))
    return rows


def copy_entries(archive,rows,target,is_zip):
    target=Path(target)
    if target.exists():raise ValueError('解包目标已存在，不覆盖已有资产')
    target.mkdir(parents=True)
    for name,is_dir,size,item in rows:
        if name is None:continue
        path=target/name
        if is_dir:path.mkdir(parents=True,exist_ok=True);continue
        path.parent.mkdir(parents=True,exist_ok=True)
        reader=archive.open(item) if is_zip else archive.extractfile(item)
        with reader as stream,path.open('xb') as output:shutil.copyfileobj(stream,output,1024*1024)
        if path.stat().st_size!=size:raise ValueError('归档成员大小不完整')


def unpack(source,target,max_bytes=512*1024**2):
    source=Path(source)
    try:
        if zipfile.is_zipfile(source):
            with zipfile.ZipFile(source) as archive:
                rows=zip_rows(archive);check_roster(rows,max_bytes);copy_entries(archive,rows,target,True)
        else:
            with tarfile.open(source,'r:*') as archive:
                rows=tar_rows(archive);check_roster(rows,max_bytes);copy_entries(archive,rows,target,False)
    except (zipfile.BadZipFile,tarfile.TarError,EOFError) as error:raise ValueError('数据集归档损坏或格式不支持') from error
    return str(Path(target).resolve())
