"""只读核对实际 reader 字段与原文件的选定行，不转换评测输入。"""
import csv
import hashlib
import json
from pathlib import Path


def source_rows(asset,row):
    path=Path(asset['path'])/row['path'];format_name=asset.get('format')
    with path.open(encoding='utf-8-sig',newline='') as stream:
        if format_name=='jsonl':result=[json.loads(line) for line in stream if line.strip()]
        elif format_name=='json':result=json.load(stream)
        elif format_name=='csv':
            header=asset.get('_csv_header')
            if type(header)!=bool:raise ValueError('CSV 原文件缺少已固定的表头约定')
            result=list(csv.DictReader(stream) if header else csv.reader(stream))
        else:raise ValueError('没有可核对的原文件格式')
    if not isinstance(result,list) or len(result)!=row['rows']:raise ValueError('原文件实际行数与准备交接不同')
    return result


def column(value,path):
    parts=path if isinstance(path,list) else [path]
    for key in parts:value=value[key]
    return value


def verify_samples(cfg,asset,task,row,chosen,actual):
    reader=cfg['reader_cfg'];keys=list(reader['input_columns'])
    output=reader.get('output_column')
    if output is not None:keys.append(output)
    keys=list(dict.fromkeys(keys))
    mapping=task.get('source_columns',{key:key for key in keys})
    if not keys or set(mapping)!=set(keys):raise ValueError('原文件字段映射须覆盖实际 reader 输入和答案列')
    original=source_rows(asset,row);observed=[]
    try:
        for index,position in enumerate(chosen):
            expected={key:column(original[position],mapping[key]) for key in keys}
            measured={key:actual[index][key] for key in keys}
            if expected!=measured:raise ValueError('实际 reader 内容与声明的原始样本行不同')
            observed.append(measured)
    except (KeyError,IndexError,TypeError) as error:
        raise ValueError('无法证明 reader 与原始行的字段映射；请提供 source_columns') from error
    raw=json.dumps(observed,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()
