"""末端自动生成与导出；只有独立 ready 回执才完成客户任务。"""
from coordination.runtime import sources
from tuning_handoff import cli as exporter
from tuning_handoff import generation, flat_publication


def deliver(session, entry, capacity, environment=None):
    configs = generation.generate(entry, capacity, environment)
    request = {'schema_version': '1.0', 'entry_manifest': entry,
               **{key: session.artifact(key, value) for key, value in configs.items()}}
    if environment:
        request['runtime_environment'] = environment
    session.artifact('handoff-request', request)
    validated = exporter.validated(request)

    def recheck():
        if exporter.validated(request) != validated:
            raise ValueError('交付源在发布期间变化')

    result = flat_publication.publish(request, validated, session.parent.parent,
                                      session.value['run_id'], recheck)
    manifest = sources.ref(result['manifest'])
    if sources.bound(manifest)['status'] != 'ready':
        raise ValueError('导出没有 ready 回执')
    session.step('delivery', 'completed', receipt=manifest)
    return result
