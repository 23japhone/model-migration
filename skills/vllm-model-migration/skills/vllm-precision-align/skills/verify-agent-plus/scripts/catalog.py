#!/usr/bin/env python3
"""在任务评测环境发现原生数据集配置；不发出推理请求。"""
import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
import uuid

sys.dont_write_bytecode = True
from precision.contract import output_parent
from precision.evidence import load, write
from precision.inputs import dataset as prepared_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--work-root', required=True, type=Path)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--plan', action='store_true')
    args = parser.parse_args()
    try:
        request = load(args.request)
        protected = [Path(request['model_path']), Path(request['dataset']['path'])]
        if 'dataset_manifest' in request:
            protected.append(Path(request['dataset_manifest']['path']))
            if not args.plan:
                protected.append(Path(prepared_dataset(request['dataset_manifest'])['path']))
        parent = output_parent(args.work_root, args.run_id, protected)
        if args.plan:
            print(json.dumps({'status': 'not_run', 'operation': 'catalog'}))
            return 0
        from precision.runtime.evalscope.catalog import inspect
        attempt = parent / ('catalog-' + uuid.uuid4().hex)
        attempt.mkdir(parents=True)
        with redirect_stdout(sys.stderr):
            result = inspect(request, attempt)
        path = attempt / 'catalog.json'
        write(path, result)
        print(json.dumps({'status': 'completed', 'manifest': str(path), 'result': result}, ensure_ascii=False, default=str))
        return 0
    except (OSError, ValueError, ImportError, KeyError, TypeError) as error:
        print(json.dumps({'status': 'blocked', 'reason': str(error)}, ensure_ascii=False))
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
