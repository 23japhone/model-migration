"""覆盖不能用‘每层至少一个 API’代替真实调用全集。"""
from pathlib import Path
import sys
import unittest

VERIFY = next((Path(__file__).resolve().parents[3]/'skills').rglob('verify-agent/scripts/verify.py')).parent
sys.path.insert(0,str(VERIFY))
from verification.runtime.capture.adapters.coverage import verify


class CoverageTests(unittest.TestCase):
    def fixture(self):
        names=['Module.model.layers.0.Block.forward.0','Torch.add.0.forward','Torch.add.1.forward']
        observed={'calls':{'input':names,'output':names},'modules':{'begin':['model.layers.0'],'end':['model.layers.0']},'scope':'registered'}
        calls=[{'call':name,'module':'model.layers.0' if i==0 else None,
                'tensors':{'input_args':[{'shape':[1]}],'input_kwargs':[],'output':[{'shape':[1]}]},'values':[]}
               for i,name in enumerate(names)]
        return observed,calls

    def test_second_api_missing_is_incomplete(self):
        observed,calls=self.fixture()
        with self.assertRaisesRegex(ValueError,'漏写'):verify(observed,calls[:-1])

    def test_unclosed_module_is_incomplete(self):
        observed,calls=self.fixture();observed['modules']['end']=[]
        with self.assertRaisesRegex(ValueError,'模块前向'):verify(observed,calls)

    def test_missing_container_module_is_incomplete(self):
        observed,calls=self.fixture()
        for side in ('begin','end'): observed['modules'][side].append('language_model.model')
        with self.assertRaisesRegex(ValueError,'实际模块调用'): verify(observed,calls)

    def test_complete_scope_does_not_claim_device_kernel_coverage(self):
        observed,calls=self.fixture();result=verify(observed,calls)
        self.assertEqual(result['calls'],3)
        self.assertEqual(result['device_kernels'],'not_observed')
