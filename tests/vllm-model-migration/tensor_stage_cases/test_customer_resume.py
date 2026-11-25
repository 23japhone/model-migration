"""同一客户 run 只改容差，通过公开 migrate.py 复算一份已保存的三遍现场。"""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_tensor_stage_fixture import bind, generate, get, put

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / 'skills/vllm-model-migration/scripts'
sys.path.insert(0, str(SCRIPTS))
from customer_workflow.contract import normalize
from customer_workflow.tensor_alignment.continuation import context_identity


class CustomerResume(unittest.TestCase):
    def test_only_tensor_thresholds_change_without_model_or_devices(self):
        with TemporaryDirectory() as temp:
            folder = Path(temp).resolve()
            generated = get(generate(folder/'evidence'))
            old_stage = bind(Path(generated['runs']['04-gpu-outlier']['manifest']))
            old_token = bind(Path(generated['second_pass']['manifest']))
            stage = get(old_stage)
            raw = {'model': {}, 'environment': {}, 'accuracy': {'tensor_alignment': {'atol': 0, 'rtol': 0}}}
            task = folder/'task.json'
            old_task = put(task, raw)
            context = {'schema_version': '1.0', 'observations': {}, 'evidence': [],
                       'intent': {'deployment': True, 'accuracy': True, 'performance': False, 'conflicts': []}}
            files = {key: put(folder/(key+'.json'), {}) for key in ('benchmark_plan', 'accuracy_plan', 'deployment')}
            exported = put(folder/'ready.json', {'status':'ready','deliverables':files})
            previous = put(folder/'prior.json', {'task_id':'vllm-model-migration-customer','status':'completed',
                'run_id':'synthetic','task_source':old_task,'inputs':normalize(raw),'declared_inputs':normalize(raw),
                'tensor_context_identity':context_identity(context),'tensor_context_evidence':[],
                'deliverables':files,'export_manifest':exported['path'],
                'task_quality':[{'dataset':'fixture','status':'completed','report':stage['inputs']['quality_report']}],
                'token_alignment':[{'status':'completed','stage':old_token,'token_report':stage['inputs']['token_report'],
                                    'quality_report':stage['inputs']['quality_report']}],
                'steps': {'delivery':{'status':'completed','receipt':exported},
                          'tensor-0':{'status':'completed','receipt':old_stage}}})
            raw['accuracy']['tensor_alignment']['atol'] = 4
            context['task_source'] = put(task, raw)
            ctx = put(folder/'context.json', context)
            command = [sys.executable,'-B',str(SCRIPTS/'migrate.py'),'--task',str(task),'--context',ctx['path'],
                       '--resume',previous['path'],'--work-root',str(folder/'work'),'--run-id','synthetic']
            result = subprocess.run(command, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            receipt = json.loads(result.stdout)
            current = receipt['tensor_alignment'][0]
            self.assertTrue(current['simulated'])
            self.assertEqual(current['comparison_result'], 'aligned')
            self.assertEqual(current['plans'], stage['plans'])
            self.assertEqual(receipt['deliverables'], files)
            conclusion = receipt['task_quality_conclusion']
            self.assertEqual(conclusion['precision_target'], 'fail')
            self.assertEqual(conclusion['reports'][0]['report'], stage['inputs']['quality_report'])
            self.assertEqual(json.loads(Path(receipt['evidence']['manifest']).read_text())['task_quality_conclusion'], conclusion)
            self.assertNotIn('task_quality_conclusion', get(previous))
            self.assertEqual(set(receipt['steps']), {'delivery','tensor-0'})

            good = next(get(source) for source in stage['inputs']['evidence']
                        if get(source).get('hardware') == 'gpu')
            malformed_cases = [None, {'subset': 'another-subset', 'sample_id': []}]
            context['evidence'] = [put(folder/f'bad-gpu-{i}.json', {**good, 'case': case})
                                   for i, case in enumerate(malformed_cases)]
            put(folder/'context.json', context)
            retry = subprocess.run(command, capture_output=True, text=True, timeout=90)
            self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
            current = json.loads(retry.stdout)['tensor_alignment'][0]
            self.assertEqual(current['comparison_result'], 'aligned')
            diagnostics = get(current['tensor_report'])['uncovered']
            self.assertEqual([issue['source'] for issue in diagnostics], context['evidence'])
            self.assertTrue(all(issue['reason'] for issue in diagnostics))
