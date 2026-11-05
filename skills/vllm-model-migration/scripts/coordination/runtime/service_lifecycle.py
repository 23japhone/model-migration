"""本次迁移服务的收尾策略；停止效果只委托 verify-agent 公开 CLI。"""

import sys as _release_sys
from pathlib import Path as _ReleasePath
_release_sys.dont_write_bytecode = True
_release_sys.path.insert(0, str(_ReleasePath(__file__).resolve().parents[3] / "scripts"))
from skill_paths import plugin_root as release_root, skill_root as release_skill, owned_files, registration_bundle
from pathlib import Path
import sys
import uuid

from . import receipts, sources
from .evidence import EvidenceError, defer_interrupts, mark_cancelled, write
from .process import call


def current_invocation(attempt, work_root):
    invocation = sources.read(attempt/'migration.invocation.json')
    root = Path(work_root)/invocation['run_id']/'vllm-migration-execute'
    if invocation['root'] != str(root) or root.resolve() != root:
        raise ValueError('迁移调用目录身份不符')
    if sources.bound(invocation['request_source']) != invocation['request']:
        raise ValueError('迁移调用输入已变化')
    return invocation, root


def migration_source(manifest, attempt, work_root):
    row = manifest['stages']['migration']
    reused = 'result' in row.get('source', {})
    if reused:
        return row.get('receipt') or row['source']['result'], None, True
    invocation, root = current_invocation(attempt, work_root)
    source = row.get('receipt')
    if source is None:
        process = sources.read(attempt/'migration.process.json')
        if process.get('interrupted') not in ('KeyboardInterrupt','TimeoutExpired'):
            raise ValueError('缺少可核验的中断调用，不能从磁盘猜测服务归属')
        candidates = list(root.glob('*/migration-manifest.json'))
        if len(candidates) != 1:
            raise ValueError('本次迁移缺少唯一可核验终态回执')
        source = sources.ref(candidates[0])
    path = Path(source['path'])
    if not path.is_relative_to(root) or len(path.relative_to(root).parts) != 2:
        raise ValueError('迁移回执不属于本次调用')
    return source, invocation, False


def deployment(manifest, attempt, work_root):
    source, invocation, reused = migration_source(manifest, attempt, work_root)
    value = sources.bound(source)
    receipts.validate(value, Path(source['path']), 'migration', 'vllm-migration-execute')
    if invocation and (value['run_id'] != invocation['run_id'] or value['inputs'] != invocation['request']):
        raise ValueError('迁移调用与回执 run/input 不符')
    if not value.get('deployment'):
        assert_not_created(value)
        return {'source':source, 'resource':None, 'reused':reused}
    original = sources.bound(value['inputs']['source']) if 'source' in value['inputs'] else value
    raw = receipts.child(original['tasks']['verify'], 'verify-agent', original['run_id'])
    handoff = value['deployment']
    expected = {'path':raw['evidence']['handoff'], 'sha256':raw['detailed_report']['handoff_sha256']}
    if handoff != expected or raw.get('operation') != 'verify':
        raise ValueError('部署交接未绑定原始 verify 操作')
    result = sources.bound(handoff)
    resource = result['resource']
    if resource['ownership'] != raw['inputs']['service']['ownership']:
        raise ValueError('资源所有权与验证请求不符')
    return {'source':source, 'handoff':handoff, 'resource':resource,
            'reused':reused, 'reported_at':value.get('finished_at'), 'observed_at':raw.get('finished_at'),
            'final_service':result.get('final_service', {})}


def assert_not_created(value):
    row = value['tasks'].get('verify', {})
    if row.get('status') in ('not_run','blocked','skipped') and not (row.get('receipt') or row.get('manifest')):
        return
    raw = receipts.child(row, 'verify-agent', value['run_id'])
    if raw is not None and unstarted_process(raw):
        return
    raise ValueError('verify 已调用或调用状态不明，但缺少可授权部署交接；资源状态未知')


def unstarted_process(raw):
    detail = raw.get('detailed_report', {})
    resource = detail.get('resource', {})
    return (raw.get('operation') == 'verify' and raw['conclusions'].get('lifecycle') == 'pass'
            and not detail.get('cleanup_error') and resource.get('kind') == 'process'
            and resource.get('ownership') == 'owned' and 'identity' in resource
            and resource['identity'] is None and detail.get('final_service') == {'state':'not_started'})


def stop_receipt(text, code, request, root, run_id, resource):
    printed = sources.parse(text)
    required = {'report_path','status','success','summary','detailed_report','conclusions'}
    if not required <= printed.keys():
        raise ValueError('stop stdout 缺少共同结果')
    path = Path(printed['report_path'])
    if not path.is_absolute() or not path.is_relative_to(root) or len(path.relative_to(root).parts) != 2:
        raise ValueError('stop 回执不属于本次独立运行')
    value = sources.read(path)
    receipts.common(value, 'verify-agent')
    expected = {'run_id':run_id, 'operation':'stop', 'inputs':request, 'report_path':str(path)}
    if any(value.get(key) != item for key,item in expected.items()):
        raise ValueError('stop run/task/input/operation 身份不符')
    if any(printed[key] != value.get(key) for key in required):
        raise ValueError('stop stdout 与原始报告不一致')
    for evidence in value['evidence'].values():
        sources.stable_bytes(evidence)
    stopped_result(value, code, resource)
    return value, sources.ref(path)


def stopped_result(value, code, resource):
    if code != 0 or value.get('success') is not True or value['status'] != 'completed':
        raise ValueError('stop 没有成功完成，资源释放未确认')
    detail = value['detailed_report']
    if not isinstance(detail, dict):
        raise ValueError('stop 详细报告须为对象')
    if (value['conclusions'] != {'lifecycle':'pass'} or detail.get('state') != 'stopped'
            or detail.get('identity') != resource['identity']):
        raise ValueError('stop 生命周期或释放身份不符')


def stop_owned(item, manifest, request, attempt, work_root, protected):
    entry = release_skill('verify-agent')/'scripts/verify.py'
    implementation = sources.ref(entry)
    run_id = manifest['run_id'][:40]+'-stop-'+uuid.uuid4().hex[:12]
    root = work_root/run_id/'verify-agent'
    sources.protect(root, protected)
    if root.exists():
        raise ValueError('stop 输出已存在')
    stop_request = {'handoff':item['handoff']}
    path = attempt/'service-stop.request.json'
    write(path, stop_request)
    argv = [sys.executable, '-B', str(entry), '--request', str(path), '--work-root', str(work_root),
            '--run-id', run_id, '--action', 'stop']
    timeout = min(request.get('timeout_seconds', 14400), 120)
    code, text = call([*argv, '--plan'], min(timeout, 60), attempt, 'service-stop-plan')
    plan = sources.parse(text)
    if code != 0 or plan != {'status':'not_run','operation':'stop','request':stop_request}:
        raise ValueError('stop 公开计划拒绝输入或身份不符')
    # 再次核对交接摘要，不能把计划到执行之间被替换的内容当作原授权。
    sources.bound(item['handoff'])
    code, text = call(argv, timeout, attempt, 'service-stop')
    value, source = stop_receipt(text, code, stop_request, root, run_id, item['resource'])
    if sources.ref(entry) != implementation:
        raise ValueError('verify-agent 入口在停止期间变化')
    write(attempt/'service-stop.implementation.json', {'entry':implementation,
          'reported_version':value.get('skill_version'), 'schema_version':value['schema_version']})
    return source, value.get('finished_at')


def retained_state(item):
    final = item['final_service']
    if not isinstance(final, dict):
        raise ValueError('来源交接的最终服务观察须为对象')
    if final.get('state') == 'retained' and final.get('resource') == item['resource']:
        return 'retained'
    if final.get('state') == 'stopped' and final.get('identity') == item['resource'].get('identity'):
        return 'stopped'
    raise ValueError('来源交接缺少可核验的最终服务观察')


def apply_policy(request, manifest, attempt, work_root, protected):
    item = deployment(manifest, attempt, work_root)
    if item['resource'] is None:
        manifest['lifecycle'].update(state='not_created', source=item['source'])
        return
    ownership = 'borrowed' if item['reused'] else item['resource']['ownership']
    lifecycle = manifest['lifecycle']
    lifecycle.update(ownership=ownership, source=item['source'], handoff=item['handoff'],
                     original_ownership=item['resource']['ownership'], reported_at=item.get('reported_at'),
                     observed_at=item.get('observed_at'))
    if ownership not in ('owned','borrowed'):
        raise ValueError('资源所有权无效')
    lifecycle.update(observation='来源交接观察，不声明当前持续存活')
    if ownership == 'owned' and request.get('service_retention', 'keep') == 'stop':
        source, observed = stop_owned(item, manifest, request, attempt, work_root, protected)
        lifecycle.update(state='stopped', stop_receipt=source, observed_at=observed, reported_at=observed,
                         observation='公开 stop 重新核对身份并确认释放')
    else:
        lifecycle['state'] = retained_state(item)
    manifest['conclusions']['lifecycle'] = 'pass'
    manifest['resources'].append({'source':lifecycle.get('stop_receipt', item['source']),
        'reported_at':lifecycle.get('reported_at'), 'observed_at':lifecycle.get('observed_at'),
        'observation':lifecycle['observation'], 'resource':item['resource'],
        'ownership':ownership, 'state':lifecycle['state']})


def failed(manifest, error):
    manifest['lifecycle'].update(state='unknown', diagnostic={'type':type(error).__name__,
                                'message':'服务收尾未确认；查看本次交接和公开 stop 原始证据'})
    manifest['conclusions']['lifecycle'] = 'fail'
    manifest.update(status='failed', success=False, next_action='inspect_service_finalization')


def finalize(request, manifest, attempt, work_root, protected, cancelled=False):
    manifest['lifecycle'] = {'policy':request.get('service_retention','keep'), 'state':'not_applicable'}
    if manifest['operation'] == 'analyze' or 'migration' not in manifest['stages']:
        if cancelled:
            mark_cancelled(manifest, ['before_service_finalization'])
        return
    row = manifest['stages']['migration']
    if not cancelled and not row.get('receipt') and not (attempt/'migration.invocation.json').exists():
        manifest['lifecycle']['state'] = 'not_created'
        return
    received = []
    try:
        if cancelled:
            with defer_interrupts(received):
                apply_policy(request, manifest, attempt, Path(work_root), protected)
        else:
            apply_policy(request, manifest, attempt, Path(work_root), protected)
    except EvidenceError:
        raise  # 共享证据无法保存时禁止继续效果。
    except KeyboardInterrupt as error:
        cancelled = True
        failed(manifest, error)
    except (*sources.ERRORS, RuntimeError) as error:
        failed(manifest, error)
    if cancelled or received:
        mark_cancelled(manifest, received or ['before_service_finalization'])
