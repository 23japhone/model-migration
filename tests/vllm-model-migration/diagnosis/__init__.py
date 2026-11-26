"""全量 unittest 发现会先加载本同名测试包，让子模块仍从实际诊断源码加载。"""
from pathlib import Path

__path__.append(str(Path(__file__).resolve().parents[3] /
                    'skills/vllm-model-migration/skills/vllm-precision-align/scripts/diagnosis'))
