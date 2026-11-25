"""同一 scope 两科各一题，历史重读只核对本地原生文件。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = (Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration/skills/vllm-precision-align'
           / 'skills/verify-agent-plus/scripts')
sys.path.insert(0, str(SCRIPTS))

from precision.evidence import write
from precision.quality import history, runtime
from precision.quality.native import measure
from precision.quality.protocol import ref, stable_ids
from precision.quality.run import project


class NativeScope(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.run = self.base/'original'
        self.root = self.run/'scope'
        self.tasks = []
        self.ids = {'a': ['explicit-id'], 'b': ['test.jsonl#0']}
        self.spec = {'metric': 'accuracy', 'sample_metric': 'acc', 'unit': 'ratio', 'direction': 'higher',
                     'sample_kind': 'binary', 'subset': None,
                     'selector': {'name':'accuracy','aggregation':'mean','dimensions':{}}}
        self.rows = ['a','b']
        for name in self.rows:
            self.prepare_sample(name)
        item = {'requested':1,'succeeded':1,'errored':0}
        metric = {'identity': self.spec['selector'], 'num':2,'score':0.5,
                  'semantics': {'kind':'quality','raw_unit':'ratio','direction':'higher_is_better'},
                  'categories':[{'subsets':[{'name':s,'num':1,'score':int(s == 'a')} for s in self.rows]}]}
        report = {'schema_version':2,'dataset_name':'demo','model_name':'e',
                  'metrics':[metric],'execution_summary':{'requested':2,'succeeded':2,'errored':0,
                  'incomplete':False,'subsets':{s:item for s in self.rows}}}
        self.save(self.root/'evalscope/reports/e/demo.json', report)

    def save(self, path, obj):
        path.parent.mkdir(parents=True, exist_ok=True)
        write(path, obj)
        return ref(path)

    def prepare_sample(self, name):
        msgs = [{'role':'user','content':name}]
        target = '4'
        answer = '4' if name == 'a' else '3'
        snap = self.save(self.run/(name+'.json'), [{'input':msgs,'target':target}])
        self.tasks.append({'subset':name,'sample_ids':[0],'benchmark':'demo','model_abbr':'e',
                           'sample_snapshot':snap,'max_model_len':128,'served_model':'served'})
        assistant = {'role':'assistant','content':answer}
        pred = {'index':0,'model':'served','messages':msgs+[assistant],
                'model_output':{'choices':[{'stop_reason':'stop','message':assistant}]}}
        rev = {'index':0,'target':[target],'sample_score':{'sample_id':0,'score':{'status':'success',
               'prediction':answer,'value':{'acc':name=='a'}}}}
        for folder, obj in [('predictions',pred), ('reviews',rev)]:
            file = self.root/'evalscope'/folder/'e'/('demo_'+name+'.jsonl')
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(json.dumps(obj)+'\n')
    def test_joint_scores_and_observed_requests(self):
        result = measure(self.root,self.tasks,self.ids,[self.spec,{**self.spec,'subset':'b'}])
        self.assertEqual(result['formal_requests'], 2)
        self.assertEqual(result['request_count_source'], 'evalscope.execution_summary.requested')
        self.assertFalse((self.root/'http-capture').exists())
        self.assertEqual([s['value'] for s in result['scores']], [0.5,0])
        self.assertEqual(result['scores'][0]['samples'][1]['sample_id'], 'test.jsonl#0')

    def test_empty_optional_per_question_scores_leave_report_total_valid(self):
        path = self.root/'evalscope/reviews/e/demo_a.jsonl'
        row = json.loads(path.read_text())
        row['sample_score']['score']['value'] = {}
        path.write_text(json.dumps(row)+'\n')
        result = measure(self.root,self.tasks,self.ids,[self.spec])['scores'][0]
        self.assertEqual(result['value'], 0.5)
        self.assertEqual(result['sample_errors'][0]['sample_id'], 'explicit-id')

    def test_duplicate_inference_invalidates_result(self):
        report_path = self.root/'evalscope/reports/e/demo.json'
        report = json.loads(report_path.read_text())
        report['execution_summary']['requested'] += 1
        self.save(report_path, report)
        with self.assertRaisesRegex(ValueError,'覆盖'):
            measure(self.root,self.tasks,self.ids,[self.spec])

    def original(self):
        result = measure(self.root,self.tasks,self.ids,[self.spec])
        protocol = {'dataset':'demo','subsets':self.rows}
        manifest = {'task_id':'verify-agent-plus','operation':'quality-run','status':'completed',
                    'conclusions':{'evaluation_validity':'pass'},'evidence':{},
                    'inputs':{'protocol':protocol,'scores':[self.spec]},
                    'detailed_report':{'hardware':'npu','scope_root':str(self.root),'context_validation':'pass',
                    'tasks':self.tasks,'stable_samples':self.ids,'files':result['files']}}
        index = {'tasks':self.tasks}
        before = {'generation':{}}
        result['static_conditions'] = runtime.recorded(index, before)
        bound = {'quality-index.json':index,'process-scope/process.json':{'returncode':0,
                 'argv':['python','entry.py','run',str(self.run/'effective-config.json')]},
                 'original-config.json':{},'effective-config.json':{'collect_perf':False,'debug':False},'environment.json':{},
                 'service-before.json':before,'service-after.json':before}
        for name, content in bound.items():
            f = self.save(self.run/name,content)
            result['files'][f['path']] = f['sha256']
        project(self.run,manifest,protocol,result)
        path = self.run/'precision-manifest.json'
        manifest['report_path'] = str(path)
        return self.save(path,manifest)

    def test_history_accepts_original_and_rejects_mutated_reviews_without_inference(self):
        reference = self.original()
        with patch('precision.quality.run.run_native',side_effect=AssertionError('unexpected inference')):
            code, checked = history.analyze({'source':reference}, self.base/'reuse', 'analysis')
            self.assertEqual(code,0,checked)
            self.assertEqual(checked['detailed_report']['formal_requests'],0)
            self.assertEqual(checked['inputs']['source'],reference)
            path = self.root/'evalscope/reviews/e/demo_a.jsonl'
            path.write_text(path.read_text()+'\n')
            code, refused = history.analyze({'source':reference}, self.base/'next', 'again')
            self.assertEqual(code,3,refused)

    def test_duplicate_explicit_source_id_fails_before_inference(self):
        file = self.base/'questions.jsonl'
        file.write_text('{"id":1}\n{"id":1}\n')
        asset = {'path':str(self.base),'format':'jsonl','files':[{'path':file.name,'rows':2}],
                 'selection':{'records':[{'file':file.name,'row_index':i} for i in range(2)]}}
        with self.assertRaisesRegex(ValueError,'重复'):
            stable_ids(asset)


if __name__ == '__main__':
    unittest.main()
