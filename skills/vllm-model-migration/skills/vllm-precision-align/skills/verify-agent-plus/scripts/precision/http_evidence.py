"""从原始 HTTP 字节重建结束、截断和预测一致性证据。"""
from collections import Counter
import hashlib
import json
from pathlib import Path

from .evidence import decode, load, sha256


def one_choice(value):
    choices=value.get('choices')
    if not isinstance(choices,list) or len(choices)!=1 or not isinstance(choices[0],dict):
        raise ValueError('响应必须包含一个实际 choice')
    return choices[0]


def single_choice(value):
    choice=one_choice(value)
    if choice.get('finish_reason')!='stop':raise ValueError('生成未正常结束或发生截断')
    return choice


def text_parts(choice):
    if 'text' in choice:return choice['text'],None
    message=choice.get('message')
    if not isinstance(message,dict):raise ValueError('缺少实际生成消息')
    content=message.get('content')
    if content is None and 'content' in message:content=''
    return content,message.get('reasoning_content') or message.get('reasoning')


def prediction(value):
    content,reasoning=text_parts(single_choice(value))
    for part in (content,reasoning):
        if part is not None and not isinstance(part,str):raise ValueError('生成正文不是文本')
    if content is None and reasoning is None:raise ValueError('缺少实际生成正文')
    # 与固定版本 Output.get_prediction 相同，仅核对，不改写传给评分器的预测。
    return reasoning+'</think>'+content if reasoning and content else (reasoning or content or '')


def usage(value):
    result=value.get('usage')
    if not isinstance(result,dict):raise ValueError('缺少可核验的 token 统计')
    keys=('prompt_tokens','completion_tokens','total_tokens')
    if any(type(result.get(key))!=int or result[key]<0 for key in keys):
        raise ValueError('token 统计无效')
    if result['total_tokens']!=result['prompt_tokens']+result['completion_tokens']:
        raise ValueError('token 统计不一致')
    return result


def validate_response(raw,status,project=prediction):
    try:
        if status!=200:raise ValueError('推理 HTTP 非200')
        value=decode(raw)
        project(value);usage(value)
    except (ValueError,TypeError,AttributeError) as error:
        return [str(error)]
    return []


def request_conditions(body,route,task):
    if route not in ('/v1/chat/completions','/v1/completions'):
        raise ValueError('不是声明支持的推理路由')
    if not isinstance(body.get('model'),str) or not body['model']:
        raise ValueError('请求没有实际模型名')
    if task.get('served_model') and body['model']!=task['served_model']:
        raise ValueError('实际请求模型与交接不一致')
    if body.get('stream') or body.get('n',1)!=1 or body.get('best_of',1)!=1:
        raise ValueError('请求为流式或重复生成')
    if body.get('truncate_prompt_tokens') is not None:
        raise ValueError('实际请求启用了上下文截断')


def context_limits(body,tokens,task):
    limit=task.get('max_model_len')
    if type(limit)!=int or limit<=0:raise ValueError('缺少明确的服务上下文上限')
    if tokens['total_tokens']>limit:raise ValueError('token 总数超过实际上下文上限')
    maximum=body.get('max_tokens',body.get('max_completion_tokens'))
    if type(maximum)!=int or maximum<=0 or tokens['completion_tokens']>maximum:
        raise ValueError('实际生成长度配置或回执不一致')


def raw_files(folder,row,files):
    values={}
    for relative,expected in row['files'].items():
        path=folder/relative
        if path.resolve()!=path or not path.is_relative_to(folder) or sha256(path)!=expected:
            raise ValueError('HTTP 原始证据身份不一致')
        if path.name in ('request.bin','response.bin'):
            if path.name in values:raise ValueError('HTTP 原始证据重复')
            values[path.name]=decode(path.read_bytes())
        files[str(path.relative_to(folder.parent))]=expected
    if set(values)!={'request.bin','response.bin'}:raise ValueError('缺少原始请求或响应')
    return values['request.bin'],values['response.bin']


def record_prediction(body,response):
    return prediction(response)


def check_record(folder,row,files,task,project=record_prediction):
    if row.get('method')!='POST':raise ValueError('存在额外非推理请求')
    if row.get('status')!=200 or row.get('errors'):raise ValueError('HTTP 推理失败')
    body,response=raw_files(folder,row,files)
    tokens=usage(response)
    request_conditions(body,row.get('route'),task)
    context_limits(body,tokens,task)
    actual={key:value for key,value in body.items() if key!='model'}
    return project(body,response),json.dumps(actual,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def check_capture(root,files,rows,task,project=record_prediction):
    errors=[];fingerprint=None
    try:
        folder=Path(root)/'http-capture';path=folder/'index.json';index=load(path)
        if index.get('errors'):raise ValueError('HTTP 记录器报告错误')
        records=index['requests']
        if not isinstance(records,list) or len(records)!=len(task['sample_ids']):
            raise ValueError('HTTP 推理次数与声明样本范围不一致')
        files[str(path.relative_to(root))]=sha256(path)
        actual=[check_record(folder,row,files,task,project) for row in records]
        if Counter(value[0] for value in actual)!=Counter(row['prediction'] for row in rows):
            raise ValueError('原始 HTTP 生成与原生 predictions 不一致')
        payload=json.dumps(sorted(value[1] for value in actual),ensure_ascii=False,separators=(',',':'))
        fingerprint=hashlib.sha256(payload.encode()).hexdigest()
    except (OSError,ValueError,TypeError,KeyError,AttributeError) as error:
        errors.append('HTTP 证据不可用：'+str(error))
    return errors,fingerprint
