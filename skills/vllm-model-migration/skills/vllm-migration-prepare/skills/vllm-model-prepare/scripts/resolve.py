#!/usr/bin/env python3
"""只读解析客户模型来源；下载和最终验证仍由 prepare.py 完成。"""
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from model_prepare.customer import resolve
from model_prepare.results import safe_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', '--request', type=Path, required=True, help='只包含 path/model_repository_id 的已校验模型对象')
    args = parser.parse_args()
    try:
        value = json.loads(args.model.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or not {'path', 'model_repository_id'} <= value.keys():
            raise ValueError('模型输入缺失')
        result = resolve(value)
        print(json.dumps({'status': 'resolved', 'asset_python': sys.executable, **result}, ensure_ascii=False))
        return 0
    except ModuleNotFoundError as error:
        known = {'modelscope': 'modelscope', 'huggingface_hub': 'huggingface-hub', 'modelscope_hub': 'modelscope-hub'}
        print(json.dumps({'status': 'blocked', 'reason': safe_text(error),
                          'missing_dependency': known.get(error.name)}, ensure_ascii=False))
        return 3
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps({'status': 'blocked', 'reason': safe_text(error)}, ensure_ascii=False))
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
