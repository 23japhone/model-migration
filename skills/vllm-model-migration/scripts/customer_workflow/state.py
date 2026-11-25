"""不可覆盖的客户快照、逐步台账和稳定请求文件。"""
import hashlib
import json
from pathlib import Path
import uuid
import copy

from .tensor_alignment import same_source
from coordination.runtime import sources
from coordination.runtime.evidence import write


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def load_previous(previous, source, task, run_id):
    if previous is None:
        return None
    reference = previous if isinstance(previous, dict) else sources.ref(previous)
    value = sources.bound(reference)
    if (value.get('task_id') != 'vllm-model-migration-customer' or value.get('run_id') != run_id
            or not same_source(value, source, task)):
        raise ValueError('续接必须使用同一客户输入及运行标识；变更输入需新任务')
    for row in value['steps'].values():
        for field in ('receipt', 'request'):
            if row.get(field):
                sources.bound(row[field])
    return value


class Session:
    def __init__(self, parent, source, task, run_id, previous=None, *, effective_task=None):
        self.parent = Path(parent)
        self.source = sources.ref(source)
        assets = task if effective_task is None else effective_task
        roots = [Path(source)] + [Path(row['path']) for row in assets['accuracy']['datasets']]
        if isinstance(assets['model']['path'], str):
            roots.append(Path(assets['model']['path']))
        sources.protect(self.parent, roots)
        self.previous = load_previous(previous, self.source, task, run_id)
        self.attempt = self.parent / 'attempts' / uuid.uuid4().hex
        self.attempt.mkdir(parents=True)
        self.path = self.attempt / 'customer-manifest.json'
        self.value = {'schema_version': '1.0', 'task_id': 'vllm-model-migration-customer', 'run_id': run_id,
                      'status': 'running', 'success': False, 'task_source': self.source, 'inputs': copy.deepcopy(task),
                      'steps': dict(self.previous['steps']) if self.previous else {}, 'deliverables': {},
                      'evidence': {'manifest': str(self.path)}, 'uncovered': []}
        for field in ('evalscope_environment', 'evalscope_install_selection', 'declared_inputs', 'effective_inputs', 'default_resolutions'):
            if self.previous and field in self.previous:
                self.value[field] = copy.deepcopy(self.previous[field])
        self.save()

    def artifact(self, label, value):
        path = self.parent / 'inputs' / (label + '-' + identity(value) + '.json')
        if path.exists():
            if sources.read(path) != value:
                raise ValueError('冻结输入已变化')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write('\n')
        return sources.ref(path)

    def save(self):
        write(self.path, self.value)

    def step(self, key, status, **details):
        self.value['steps'][key] = {**self.value['steps'].get(key, {}), 'status': status, **details}
        if status == 'completed':
            for field in ('decision', 'diagnostic'):
                self.value['steps'][key].pop(field, None)
        self.save()

    def ready(self, key):
        row = self.value['steps'].get(key, {})
        if row.get('status') != 'completed':
            return None
        return sources.bound(row['receipt']) if row.get('receipt') else row.get('value')

    def finish(self, status, **details):
        from .recovery import resources
        self.value['resources'] = resources(self.value['steps'])
        self.value.update(status=status, success=status == 'completed', **details)
        self.save()
        return {'completed': 0, 'failed': 1, 'blocked': 3, 'not_run': 0}[status], self.value
