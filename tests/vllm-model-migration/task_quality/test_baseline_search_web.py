import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILL = Path(__file__).resolve().parents[3] / 'skills/vllm-model-migration'
sys.path.insert(0, str(SKILL / 'scripts'))
from coordination.runtime import sources


class OfficialDiscovery(unittest.TestCase):
    def setUp(self):
        from customer_workflow.quality.baseline_search import discover
        self.discover = discover
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve() / 'search'
        self.task = {'model': {'model_repository_id': 'lab/demo', 'model_name': 'demo'}, 'accuracy': {}}
        self.rev = 'a' * 40
        self.base = 'https://huggingface.co/lab/demo/resolve/' + self.rev + '/'
        self.api = 'https://huggingface.co/api/models/lab/demo'
        self.records = {self.api: {'id': 'lab/demo', 'sha': self.rev, 'siblings': [{'rfilename': 'README.md'}]},
                        self.base + 'README.md': b'# Results\n|MMLU|75|'}
        self.urls = []
        def fetch(url, timeout):
            self.urls.append(url)
            if url not in self.records:
                raise OSError('fixture unavailable')
            item = self.records[url]
            return item if isinstance(item, bytes) else json.dumps(item).encode()
        stub = patch('customer_workflow.quality.baseline_search.transport.fetch', side_effect=fetch)
        stub.start()
        self.addCleanup(stub.stop)

    def test_model_card_summary_only_saves_missing_and_reuses_search(self):
        first = self.discover(self.task, {}, self.root)
        self.assertEqual(first['status'], 'not_evaluated')
        self.assertTrue(first['missing'])
        self.assertTrue(any(row['url'] == self.base + 'README.md' for row in first['sources']))
        count = len(self.urls)
        self.assertEqual(first, self.discover(self.task, {}, self.root))
        self.assertEqual(len(self.urls), count)

    def same_names(self, count=2):
        from urllib.parse import urlencode
        self.task['model'].pop('model_repository_id', None)
        url = 'https://huggingface.co/api/models?' + urlencode({'search': 'demo', 'limit': 100})
        self.records[url] = [{'id':'other/demo'}, {'id':'lab/demo'}] + [
            {'id': 'additional/demo-' + str(i)} for i in range(count-2)]
        other = 'https://huggingface.co/other/demo/resolve/' + self.rev + '/'
        other_api = 'https://huggingface.co/api/models/other/demo'
        self.records[other_api] = {'id':'other/demo','sha':self.rev,'siblings':[{'rfilename':'README.md'}]}
        self.records[other+'README.md'] = b'# Other results'
        return other, other_api

    def test_name_only_inspects_both_cards_even_when_index_page_has_100(self):
        other, _ = self.same_names(100)
        result = self.discover(self.task, {}, self.root)
        self.assertIn(self.base+'README.md', self.urls)
        self.assertIn(other+'README.md', self.urls)
        self.assertEqual(result['status'], 'not_evaluated')

    def test_only_one_of_two_same_named_repositories_can_prove_matching_deployment(self):
        from urllib.parse import urlencode
        from importlib import import_module
        fixture_module = (self.__module__.rpartition('.')[0]+'.test_baseline_qualification').lstrip('.')
        corpus = import_module(fixture_module).PublicBaselineQualification()
        corpus.setUp()
        self.addCleanup(corpus.doCleanups)
        name = corpus.task['model']['model_name']
        self.task['model'] = {'model_name':name}
        repos = ['lab/'+name, 'other/'+name]
        search = 'https://huggingface.co/api/models?'+urlencode({'search':name,'limit':100})
        self.records[search] = [{'id':repo} for repo in repos]
        for repo in repos:
            if repo.startswith('other/'):
                corpus.proto['model_identity'] = 'f'*64
            corpus.evidence(config_model=repo)
            names = ['eval/'+f for f in ('index.json','gpu.json','report.json','config.json')]
            base = 'https://huggingface.co/'+repo+'/resolve/'+self.rev+'/'
            self.records['https://huggingface.co/api/models/'+repo] = {
                'id':repo,'sha':self.rev,'siblings':[{'rfilename':f} for f in ['README.md', *names]]}
            self.records[base+'README.md'] = b'GPU evaluation results'
            for file in names:
                self.records[base+file] = (corpus.root/Path(file).name).read_bytes()
        observed = {'deployment_receipt':corpus.origin['deployment_receipt']}
        result = self.discover(self.task, observed, self.root)
        self.assertEqual(result['status'], 'found', result['missing'])
        self.assertEqual(result['baselines'][0]['origin']['repository_id'], repos[0])
        self.assertIn('权重', ' '.join(result['missing']))

    def test_matching_distinct_publishers_remain_ambiguous_even_for_equal_scores(self):
        other, other_api = self.same_names()
        for base, api in ((self.base,self.api),(other,other_api)):
            self.records[base+'eval/results.json'] = {}
            self.records[api]['siblings'].append({'rfilename':'eval/results.json'})
        verified = {'baseline':{'dataset':'quiz'},'protocol':{'dataset':'quiz'},'missing':[]}
        with patch('customer_workflow.quality.baseline_search.candidate', return_value={'path':'unit-fixture'}), \
                patch('customer_workflow.quality.baseline_search.qualify', return_value=verified):
            result = self.discover(self.task, {}, self.root)
        self.assertEqual(result['status'], 'not_evaluated')
        self.assertFalse(result['baselines'])
        reason = ' '.join(result['missing'])
        for name in ('lab/demo', 'other/demo', '不能唯一'):
            self.assertIn(name, reason)

    def test_fixed_model_source_prefers_repository_only_if_its_weights_match_deployment(self):
        from customer_workflow.state import identity
        self.same_names()
        records = [{'path':'model.safetensors','size':123,'sha256':'a'*64}]
        provenance = {'provider':'huggingface','repository':'lab/demo','source_files':records,
                      'snapshot_identity':identity(records)}
        observed = {'model_resolution':{'asset':{'provenance':provenance}},
                    'deployment_receipt':{'path':'fixture', 'sha256':'b'*64}}
        deployed = {'files':{'model.safetensors':'a'*64},'weight_files':['model.safetensors']}
        with patch('customer_workflow.quality.baseline_search.qualification.deployed_model', return_value=deployed):
            first = self.discover(self.task, observed, self.root)
            self.assertNotIn('https://huggingface.co/api/models/other/demo', self.urls)
            self.assertEqual(first['model_source']['repository'], 'lab/demo')
            self.urls.clear()
            deployed['files']['model.safetensors'] = 'f'*64
            self.discover(self.task, observed, self.root)
        self.assertIn('https://huggingface.co/api/models/other/demo', self.urls)

    def test_attached_absolute_archive_transports_only_bound_raw_files(self):
        def add(name, data):
            raw = json.dumps(data).encode()
            self.records[self.base + 'eval/' + name] = raw
            return {'path': '/original/gpu/' + name, 'sha256': hashlib.sha256(raw).hexdigest()}
        cfg = add('config.json', {'model_id': 'demo'})
        report = add('report.json', {'schema_version': 2})
        origin = add('origin.json', {'files': {cfg['path']: cfg['sha256'], report['path']: report['sha256']}})
        index = {'format': 'evalscope-gpu-history-v1', 'source': origin, 'config': cfg, 'report': report}
        self.records[self.base + 'eval/history.json'] = index
        self.records[self.api]['siblings'].extend({'rfilename': 'eval/' + n}
                     for n in ('history.json', 'origin.json', 'config.json', 'report.json'))
        def qualify(task, origin, ref):
            self.assertEqual(origin['repository_id'], 'lab/demo')
            self.assertEqual(origin['artifact_url'], self.base + 'eval/history.json')
            localized = sources.bound(ref)
            raw_source = sources.bound(localized['source'])
            self.assertEqual(raw_source['files'][localized['report']['path']], report['sha256'])
            self.assertEqual(sources.bound(localized['report']), {'schema_version': 2})
            return {'baseline': {'dataset': 'quiz'}, 'missing': [], 'protocol': {'dataset': 'quiz'},
                    'static_conditions': {}, 'evidence': {}}
        with patch('customer_workflow.quality.baseline_search.qualify', side_effect=qualify):
            result = self.discover(self.task, {}, self.root)
        self.assertEqual(result['status'], 'found')
        self.assertEqual(result['baselines'][0]['protocol']['dataset'], 'quiz')

    def test_unreferenced_external_attachment_never_requested(self):
        url = 'https://github.com/whoever/lab/blob/main/history.json'
        result = self.discover(self.task, {'quality_baseline_sources': [url]}, self.root)
        self.assertEqual(result['status'], 'not_evaluated')
        self.assertFalse(any('whoever' in u for u in self.urls))

    def test_cached_publication_must_still_match_its_digest(self):
        first = self.discover(self.task, {}, self.root)
        attachment = next(row['payload'] for row in first['sources'] if row['url'].endswith('README.md'))
        Path(attachment['path']).write_bytes(b'# replaced')
        count = len(self.urls)
        again = self.discover(self.task, {}, self.root)
        self.assertEqual(again['status'], 'not_evaluated')
        self.assertIn('SHA256', ' '.join(again['missing']))
        self.assertEqual(count, len(self.urls))

    def test_model_card_links_bind_external_report_to_actual_repository_commit(self):
        commit = 'b' * 40
        self.records[self.base + 'README.md'] = b'[scores](https://github.com/lab/eval/blob/main/mmlu.json)'
        api = 'https://api.github.com/repos/lab/eval'
        self.records[api + '/commits/main'] = {'sha': commit}
        self.records[api + '/git/trees/' + commit + '?recursive=1'] = {
            'tree': [{'type': 'blob', 'path': 'mmlu.json'}]}
        report = 'https://raw.githubusercontent.com/lab/eval/' + commit + '/mmlu.json'
        self.records[report] = {'schema_version': 2, 'metrics': [{}]}
        result = self.discover(self.task, {}, self.root)
        self.assertEqual(result['status'], 'not_evaluated')
        self.assertIn(report, self.urls)
        self.assertIn('同轮 GPU', ' '.join(result['missing']))

    def test_relative_model_card_link_can_name_an_arbitrary_report_file(self):
        self.records[self.base + 'README.md'] = b'[Raw evaluation](./files/gsm8k.json)'
        self.records[self.api]['siblings'].append({'rfilename': 'files/gsm8k.json'})
        self.records[self.base + 'files/gsm8k.json'] = {'schema_version': 2, 'metrics': [{}]}
        self.discover(self.task, {}, self.root)
        self.assertIn(self.base + 'files/gsm8k.json', self.urls)

    def test_incidental_link_is_not_proof_of_third_party_benchmarks(self):
        self.records[self.base + 'README.md'] = b'[Inference engine](https://github.com/other/engine)'
        extra = {'quality_baseline_sources': ['https://github.com/other/engine/blob/main/claimed.json']}
        self.discover(self.task, extra, self.root)
        self.assertFalse(any('other/engine' in url for url in self.urls))
