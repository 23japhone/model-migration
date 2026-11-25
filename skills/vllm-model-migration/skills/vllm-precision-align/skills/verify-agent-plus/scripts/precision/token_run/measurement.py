"""一个原生评测请求绑定一条捕获记录和同轮评分。"""
from copy import deepcopy
from pathlib import Path
import uuid

from ..evidence import bound, decode, load, verify_files, write
from ..quality.native import quality_reviews
from ..quality.projection import sample_score
from ..runtime.capture import recording
from ..runtime.evalscope.binding import messages_identity, target_values
from ..runtime.evalscope.completion import http_record, validate_response
from ..runtime.evalscope.results import native_prediction
from ..runtime.process import execute
from .capture import invoke, post_pair


def prepare_case(config, path, proxy):
    config = deepcopy(config)
    config.update(api_url=proxy + '/v1', work_dir=str(path / 'native'), no_timestamp=True, eval_batch_size=1)
    generation = config['generation_config']
    generation['extra_body'] = {**(generation.get('extra_body') or {}), 'return_token_ids': True}
    return config


def grade(root, task, index, pair, expected, specs):
    stem = f"{task['benchmark']}_{task['subset']}.jsonl"

    def native_path(category):
        return root / 'native' / category / task['model_abbr'] / stem

    predictions = [decode(line) for line in native_path('predictions').read_text().splitlines() if line.strip()]
    reviews = quality_reviews(native_path('reviews'))
    if len(predictions) != 1 or len(reviews) != 1:
        raise ValueError('定向评测未取得唯一原生 prediction 和 review')
    prediction, review = predictions[0], reviews[0]
    response = bound(pair['response'])
    if prediction['index'] != index or review['id'] != index or prediction['model'] != task['served_model']:
        raise ValueError('原生题号或提供服务的模型与实际请求不一致')
    texts = native_prediction(prediction)
    prompt, actual, finish = http_record(bound(pair['request']), response)
    expected_finish = {'stop': 'stop', 'max_tokens': 'length'}[prediction['model_output']['choices'][0]['stop_reason']]
    if prompt != messages_identity(expected['input']) or finish != expected_finish:
        raise ValueError('HTTP 输入或停止原因与冻结题目和原生预测不同')
    if (texts != actual or texts[0] != review['sample_score']['score']['prediction']
            or review.get('target') != target_values(expected['target'])):
        raise ValueError('实际模型回答与原生评分记录不一致')
    values = review['sample_score']['score']['value']
    return {spec['metric']: sample_score(values, spec) for spec in specs if spec['subset'] in (None, task['subset'])}


def measure_case(folder, request, source, selected, task, index):
    trace = 'tok-' + uuid.uuid4().hex
    start = {'operation': 'arm', 'source': source, 'sample_key': selected['sample_id'], 'native_id': index,
             'trace_id': trace, 'profile': 'token_prediction'}
    config = load(task['config'])
    expected = bound_list(task['sample_snapshot'])[index]
    http = folder / 'http'
    timeout = request['measurement']['evaluator'].get('timeout_seconds', 14400)
    invoke(folder, start)
    with recording(task['endpoint'], http, timeout, validator=validate_response, trace_id=trace) as proxy:
        path = folder / 'eval.json'
        payload = {'config': prepare_case(config, folder, proxy), 'benchmark': task['benchmark'],
                   'subset': task['subset'], 'index': index, 'expected': expected}
        write(path, payload)
        entry = Path(__file__).with_name('native.py')
        argv = [request['measurement']['evaluator']['python'], '-I', '-B', str(entry), str(path)]
        execute(argv, folder / 'process', timeout)
    pair = post_pair(load(http / 'index.json'), http)
    seal = invoke(folder, {'operation': 'seal', 'source': source, 'trace_id': trace, 'http': pair})
    record = seal['token_sample']
    if bound(record)['sample_id'] != selected['sample_id']:
        raise ValueError('捕获的题目身份不匹配')
    return record, grade(folder, task, index, pair, expected, request['scores'])


def bound_list(reference):
    verify_files({reference['path']: reference['sha256']})
    return load(reference['path'])
