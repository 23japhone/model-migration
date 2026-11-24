"""三步定界编排：版本探测、渲染反解、原始生成对照与九类判定。"""
from . import evidence
from . import inputs
from .checks import render as render_checks
from .checks import request as request_check
from .judge import coverage as coverage_judge
from .judge import decide as decide_judge
from .judge import verdicts
from .probe import capability
from .probe import http as http_probe
from .probe import replay

# 一次定界的请求预算（契约预写，见 references/contract.md）；不靠重试放大。
REQUEST_BUDGET = 10

EVIDENCE_LIMITATION = {
    'render': '',
    'tokenize': 'token_ids 来自 /tokenize; 部分版本不透传 tools/chat_template_kwargs, '
                '与 chat 实际渲染口径可能有差异',
}


class DemarcationUnavailable(Exception):
    """服务不可达或渲染接口不可用，本次无法执行。"""


def _save(attempt, name, value):
    path = attempt / name
    evidence.write(path, value)
    return str(path)


def _discover_model(client):
    ok, data = client.request('/v1/models')
    if ok and isinstance(data, dict):
        for item in data.get('data', []) or []:
            if isinstance(item, dict) and item.get('id'):
                return item['id']
    return None


def _render(client, contract, model, badcase, render_mode):
    order = ['render', 'tokenize'] if render_mode == 'auto' else [render_mode]
    fallback = []
    for mode in order:
        if mode == 'render':
            result = replay.render_via_endpoint(client, contract, model, badcase)
        else:
            result = replay.render_via_tokenize(client, contract, model, badcase)
        token_ids, text, tools_rendered, first_resp, second_resp, err = result
        if err is None and text:
            return mode, token_ids, text, tools_rendered, first_resp, second_resp, fallback
        fallback.append('%s 不可用' % mode)
    raise DemarcationUnavailable('全部渲染途径不可用: %s' % ' -> '.join(fallback))


def _run_step2(client, contract, model, badcase, render_mode, attempt):
    mode, token_ids, text, tools_rendered, first_resp, second_resp, fallback = \
        _render(client, contract, model, badcase, render_mode)
    checks = render_checks.check_rendered(text, badcase, tools_rendered)
    render_ok = all(check['level'] != 'FAIL' for check in checks)
    evidence_files = {}
    if first_resp is not None:
        evidence_files['rendered'] = _save(attempt, 'rendered.json', first_resp)
    if second_resp is not None:
        evidence_files['rendered_prompt'] = _save(attempt, 'rendered_prompt.json', second_resp)
    return mode, token_ids, text, checks, render_ok, evidence_files, fallback


def _run_step3(client, contract, model, badcase, max_tokens, temperature,
               token_ids, text, thresholds, attempt):
    prompt = token_ids if token_ids else text
    raw_choice, raw_resp, raw_err = replay.completions(
        client, contract, model, prompt, badcase, max_tokens, temperature)
    chat_choice, chat_resp, chat_err = replay.chat(
        client, model, badcase, max_tokens, temperature)
    raw, chat = None, None
    evidence_files = {}
    if raw_err is None:
        evidence_files['raw_completion'] = _save(attempt, 'raw_completion.json', raw_resp)
        raw = decide_judge.analyze_raw(raw_choice.get('text') or '', thresholds)
        raw['finish_reason'] = raw_choice.get('finish_reason')
    if chat_err is None:
        evidence_files['chat_response'] = _save(attempt, 'chat_response.json', chat_resp)
        chat = decide_judge.analyze_chat(chat_resp)
    return raw, chat, raw_err, chat_err, evidence_files


def _step3_uncovered(raw_err, chat_err):
    result = []
    if raw_err:
        result.append('第 3 步原始生成失败(不重发): %s' % raw_err)
    if chat_err:
        result.append('第 3 步 chat 对照失败: %s' % chat_err)
    return result


def _setup(client, model_hint):
    ver_str, ver = capability.detect_version(client)
    contract = capability.resolve_contract(ver)
    model = model_hint or _discover_model(client)
    if not model:
        raise DemarcationUnavailable('模型发现失败, 请显式指定 service.model')
    if ver is None:
        probed = capability.probe_convention(client, model)
        if probed:
            contract.update(probed)
    return ver_str, contract, model


def _generation_params(request, badcase):
    max_tokens = request.get('max_tokens') or badcase.get('max_tokens') or 512
    temperature = request.get('temperature')
    if temperature is None:
        temperature = badcase.get('temperature', 0)
    return max_tokens, temperature


def _conclude(step1_fatal, step1_fail, render_ok, raw, chat, raw_err, chat_err,
              step1_items, checks, text, mode, evidence_files, badcase):
    if raw is None or chat is None:
        return ['INCONCLUSIVE'], 'INCONCLUSIVE', _step3_uncovered(raw_err, chat_err)
    verdict_list, primary = decide_judge.decide(
        step1_fatal, step1_fail, render_ok, raw, chat, has_tools=bool(badcase.get('tools')))
    uncovered = coverage_judge.coverage(
        primary, {'items': step1_items, 'fatal': False},
        {'checks': checks, 'prompt_head': text[:120]}, raw, chat, mode, evidence_files)
    return verdict_list, primary, uncovered


def _fatal_request_result(items, advice):
    return {'conclusions': {'demarcation': 'pass'},
            'detailed_report': {'step1_request': {'items': items, 'fatal': True},
                                'verdict': ['REQUEST'], 'primary': 'REQUEST',
                                'advice': {'REQUEST': advice.get('REQUEST', '')}},
            'uncovered': ['messages 为空, 第 2/3 步未执行; 补齐请求后重新定界']}


def run(request, parent, run_id):
    attempt, manifest = evidence.begin(parent, request, run_id, 'run')
    try:
        bound = inputs.bind(request)
        badcase = bound['badcase']
        profile = verdicts.load_profile()
        thresholds = profile['thresholds']['garbage']
        advice = profile['advice']

        step1_items, step1_fatal = request_check.step1_check_request(badcase)
        if step1_fatal:
            manifest.update(_fatal_request_result(step1_items, advice))
            return evidence.finish(manifest, 'completed')
        step1_fail = request_check.step1_has_fail(step1_items)

        client = http_probe.Client(bound['endpoint'], request.get('timeout_seconds', 60), REQUEST_BUDGET)
        ver_str, contract, model = _setup(client, bound['model'])
        max_tokens, temperature = _generation_params(request, badcase)

        mode, token_ids, text, checks, render_ok, evidence_files, fallback = \
            _run_step2(client, contract, model, badcase, request.get('render_mode', 'auto'), attempt)
        raw, chat, raw_err, chat_err, step3_evidence = \
            _run_step3(client, contract, model, badcase, max_tokens, temperature,
                       token_ids, text, thresholds, attempt)
        evidence_files.update(step3_evidence)
        evidence_files['request_ledger'] = _save(attempt, 'request-ledger.json', client.ledger)

        verdict_list, primary, uncovered = _conclude(
            step1_fatal, step1_fail, render_ok, raw, chat, raw_err, chat_err,
            step1_items, checks, text, mode, evidence_files, badcase)
        manifest['uncovered'] = uncovered
        manifest['conclusions']['demarcation'] = 'pass' if primary != 'INCONCLUSIVE' else 'review_required'
        manifest['detailed_report'] = {
            'meta': {'url': bound['endpoint'], 'model': model, 'render_mode': mode,
                     'render_fallback_chain': fallback,
                     'evidence_limitation': EVIDENCE_LIMITATION.get(mode, ''),
                     'completions_prompt_form': 'token_ids' if token_ids else 'text',
                     'params': {'max_tokens': max_tokens, 'temperature': temperature},
                     'collected_at': evidence.now(), 'evidence_files': evidence_files,
                     'vllm_version': ver_str, 'tokenize_prefix': contract.get('tokenize_prefix', ''),
                     'detokenize_field': contract.get('detokenize_field', 'prompt'),
                     'request_count': client.request_count},
            'step1_request': {'items': step1_items, 'fatal': False},
            'step2_render': {'mode': mode, 'prompt_len': len(text),
                             'n_token_ids': len(token_ids) if token_ids else 0,
                             'prompt_head': text[:120], 'checks': checks, 'ok': render_ok},
            'step3_raw': raw if raw is not None else {'error': raw_err},
            'step3_chat': chat if chat is not None else {'error': chat_err},
            'has_tools': bool(badcase.get('tools')),
            'verdict': verdict_list, 'primary': primary,
            'advice': {verdict: advice.get(verdict, '') for verdict in verdict_list},
        }
        return evidence.finish(manifest, 'completed')
    except DemarcationUnavailable as error:
        manifest['diagnostic'] = {'type': 'demarcation_unavailable', 'message': str(error)}
        return evidence.finish(manifest, 'failed')
    except (OSError, ValueError, TypeError, KeyError, http_probe.RequestBudgetExceeded) as error:
        manifest['diagnostic'] = {'type': type(error).__name__, 'message': str(error)}
        return evidence.finish(manifest, 'failed')


def analyze(request, parent, run_id):
    attempt, manifest = evidence.begin(parent, request, run_id, 'analyze')
    try:
        source = evidence.bound(request['source'])
        report = source.get('detailed_report', {})
        step1 = report.get('step1_request', {})
        render = report.get('step2_render', {})
        raw = report.get('step3_raw', {})
        chat = report.get('step3_chat', {})
        if not isinstance(raw, dict) or not isinstance(chat, dict):
            raise ValueError('源结果缺少可重算的 step3_raw/step3_chat 证据')
        step1_fatal = step1.get('fatal', False)
        step1_fail = any(item.get('level') == 'FAIL' for item in step1.get('items', []))
        render_ok = render.get('ok', False)
        has_tools = report.get('has_tools', False)
        verdict_list, primary = decide_judge.decide(
            step1_fatal, step1_fail, render_ok, raw, chat, has_tools)
        advice = verdicts.load_profile()['advice']
        manifest['conclusions']['demarcation'] = 'pass' if primary != 'INCONCLUSIVE' else 'review_required'
        manifest['detailed_report'] = {
            **report, 'verdict': verdict_list, 'primary': primary,
            'advice': {verdict: advice.get(verdict, '') for verdict in verdict_list},
        }
        manifest['uncovered'] = coverage_judge.coverage(
            primary, step1, render, raw, chat,
            report.get('meta', {}).get('render_mode', ''),
            report.get('meta', {}).get('evidence_files', {}))
        return evidence.finish(manifest, 'completed')
    except (OSError, ValueError, TypeError, KeyError) as error:
        manifest['diagnostic'] = {'type': type(error).__name__, 'message': str(error)}
        return evidence.finish(manifest, 'failed')
