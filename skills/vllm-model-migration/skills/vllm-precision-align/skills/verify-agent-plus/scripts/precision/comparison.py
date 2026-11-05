"""相同协议下的指标比较；测量、可比性和目标分别判定。"""
import json


def metric_identity(value):
    return json.dumps({'identity':value.get('native_identity',value.get('selector')),
                       'semantics':value.get('native_semantics')},
                      sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)


def index(rows):
    return {(row['dataset'],key):(row['valid'],{**value,'path':value.get('path',[key]),
                'request_conditions_sha256':row.get('request_conditions_sha256')})
            for row in rows for key,value in row['metrics'].items()}


def comparable_pair(current, baseline):
    return (current and baseline and current[0] and baseline[0]
            and current[1]['unit']==baseline[1]['unit'] and current[1]['direction']==baseline[1]['direction']
            and current[1]['path']==baseline[1]['path']
            and metric_identity(current[1])==metric_identity(baseline[1])
            and current[1]['request_conditions_sha256'] is not None
            and current[1]['request_conditions_sha256']==baseline[1]['request_conditions_sha256']
            and current[1]['value'] is not None and baseline[1]['value'] is not None)


def decide_target(target, current, baseline, comparable):
    if not current or not current[0] or current[1]['value'] is None:return 'not_evaluated'
    metric=current[1];value=metric['value'];limit=target['value']
    if target['kind']=='absolute':
        if target['unit']!=metric['unit']:return 'not_evaluated'
        passed=value>=limit if metric['direction']=='higher' else value<=limit
    else:
        if not comparable or not comparable_pair(current,baseline):return 'not_evaluated'
        previous=baseline[1]['value']
        drop=previous-value if metric['direction']=='higher' else value-previous
        if target['kind']=='max_relative_drop':
            if previous==0:return 'not_evaluated'
            drop=100*drop/abs(previous)
        elif target['unit']!=metric['unit']:return 'not_evaluated'
        passed=drop<=limit
    return 'pass' if passed else 'fail'


def comparability(left, right, baseline, comparable):
    comparison='not_evaluated' if baseline is None else 'pass'
    if baseline is not None and (not comparable or set(left)!=set(right)
                                or not all(comparable_pair(value,right.get(key)) for key,value in left.items())):
        comparison='review_required'
    return comparison


def target_conclusion(rows):
    verdicts=[row['verdict'] for row in rows]
    if 'fail' in verdicts:return 'fail'
    return 'pass' if verdicts and all(x=='pass' for x in verdicts) else 'not_evaluated'


def judge(current, baseline, targets, comparable):
    left=index(current);right=index(baseline or [])
    comparison=comparability(left,right,baseline,comparable)
    rows=[]
    for target in targets:
        key=(target['dataset'],target['metric'])
        verdict=decide_target(target,left.get(key),right.get(key),comparison=='pass')
        rows.append({**target,'verdict':verdict})
    conclusion=target_conclusion(rows)
    differences=[]
    if comparison=='pass':
        differences=[{'dataset':key[0],'metric':key[1],'unit':value[1]['unit'],
                      'npu_minus_baseline':value[1]['value']-right[key][1]['value']} for key,value in left.items()]
    return {'comparability':comparison,'precision_target':conclusion,'targets':rows,'differences':differences}
