"""读取并核验原始格式，不重写题目、答案或评分逻辑。"""
import csv
import json


def validate_record(value,required):
    if not isinstance(value,dict):raise ValueError('样本必须是对象')
    if any(field not in value for field in required):raise ValueError('样本缺少请求的必需字段')


def validate_recipe_record(value,recipe,split):
    if recipe=='gsm8k':
        if any(not isinstance(value.get(k),str) or not value[k].strip() for k in ('question','answer')):raise ValueError('GSM8K 必须包含非空 question/answer')
        if '#### ' not in value['answer']:raise ValueError('GSM8K 原始 answer 缺少加载器所需分隔标记')
    elif recipe=='ceval':
        if any(not value.get(k,'').strip() for k in ('question','A','B','C','D')):raise ValueError('C-Eval 必需列为空或缺失')
        if split!='test' and value.get('answer') not in ('A','B','C','D'):raise ValueError('C-Eval 已公开答案 split 的 answer 格式无效')
    elif recipe=='mmlu':
        if any(not value.get(k,'').strip() for k in ('question','A','B','C','D')) or value.get('answer') not in ('A','B','C','D'):raise ValueError('MMLU 六列内容或答案标记无效')


def csv_records(stream,request):
    reader=csv.reader(stream)
    if request['recipe']=='mmlu':
        for row in reader:
            if len(row)!=6:raise ValueError('MMLU 必须为无表头六列 CSV')
            yield dict(zip(['question','A','B','C','D','answer'],row))
    elif request['csv_header'] or request['recipe']=='ceval':
        header=next(reader,None)
        if not header or len(set(header))!=len(header):raise ValueError('CSV 表头缺失或重复')
        for row in reader:
            if len(row)!=len(header):raise ValueError('CSV 行与表头列数不一致')
            yield dict(zip(header,row))
    else:
        names=request['required_fields']
        if not names:raise ValueError('无表头通用 CSV 必须指定 required_fields 作为列名')
        for row in reader:
            if len(row)!=len(names):raise ValueError('CSV 行与指定列名数量不一致')
            yield dict(zip(names,row))


def records(path,request):
    with path.open(encoding='utf-8-sig',newline='') as stream:
        if request['format']=='csv':yield from csv_records(stream,request)
        elif request['format']=='json':
            value=json.load(stream)
            if not isinstance(value,list):raise ValueError('通用 JSON 必须是对象列表')
            yield from value
        else:
            for line in stream:
                if not line.strip():raise ValueError('JSONL 不能包含空样本行')
                yield json.loads(line)


def row_count(path,request,split):
    count=0
    try:
        for value in records(path,request):
            validate_record(value,request['required_fields']);validate_recipe_record(value,request['recipe'],split);count+=1
    except (UnicodeError,json.JSONDecodeError,csv.Error) as error:raise ValueError(f'数据文件格式无效：{path.name}') from error
    if not count:raise ValueError(f'数据文件没有样本：{path.name}')
    return count
