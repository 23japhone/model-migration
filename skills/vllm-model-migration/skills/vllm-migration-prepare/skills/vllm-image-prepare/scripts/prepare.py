#!/usr/bin/env python3
"""准备并核验华为昇腾目标的 vLLM-Ascend 镜像资产。"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

from image_prepare.docker import Docker, prepare_image
from image_prepare.request import normalize_request
from image_prepare.results import begin_attempt, output_parent, safe_text, write_json


class ChineseArgumentParser(argparse.ArgumentParser):
    """保留参数名称，将帮助标题显示为中文。"""

    def format_help(self):
        return super().format_help().replace('usage: ', '用法：', 1).replace('options:\n', '参数：\n', 1)

    def format_usage(self):
        return super().format_usage().replace('usage: ', '用法：', 1)


def execute(request, parent, run_id):
    attempt, manifest = begin_attempt(parent, request, run_id)
    destination = attempt/'image-manifest.json'
    commands = []
    write_json(destination, manifest)

    def journal(value):
        if 'stderr' in value:
            value = {**value, 'stderr': safe_text(value['stderr'])}
        commands.append(value)
        write_json(attempt/'docker-commands.json', commands)

    code = 0
    try:
        manifest['asset'] = prepare_image(request, Docker(request['timeout_seconds'], journal))
        manifest['status'] = 'completed'
        manifest['conclusions']['assets'] = 'pass'
        registry = manifest['asset']['source_status'] == 'registry_record'
        manifest['conclusions']['source'] = 'pass' if registry else 'review_required'
        if not registry:
            manifest['uncovered'].append('镜像来源仅为声明的配方；tar 或派生镜像缺少可核对的上游 RepoDigest')
    except ImportError as error:
        code = 3
        manifest.update(status='blocked', diagnostic={'type': type(error).__name__, 'message': '依赖不可用：'+safe_text(error)})
    except (Exception, KeyboardInterrupt) as error:
        code = 1
        manifest.update(status='failed', diagnostic={'type': type(error).__name__, 'message': '镜像准备失败：'+safe_text(error)})
        manifest['conclusions']['assets'] = 'fail'
    manifest['finished_at'] = datetime.now(timezone.utc).isoformat()
    write_json(attempt/'docker-commands.json', commands)
    write_json(destination, manifest)
    return code, {'manifest': str(destination), 'status': manifest['status'], 'conclusions': manifest['conclusions']}


def main(argv=None):
    parser = ChineseArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('-h', '--help', action='help', help='显示帮助并退出')
    parser.add_argument('--request', required=True, type=Path, help='镜像请求 JSON 文件，不能包含凭据')
    parser.add_argument('--work-root', required=True, type=Path, help='位于技能包外的运行证据根目录')
    parser.add_argument('--run-id', required=True, help='各准备任务共用的运行标识')
    parser.add_argument('--plan', action='store_true', help='只核验请求和证据路径，不调用 Docker、联网或写入文件')
    args = parser.parse_args(argv)
    try:
        request = normalize_request(json.loads(args.request.read_text(encoding='utf-8-sig')), args.work_root.expanduser().resolve())
        parent = output_parent(args.work_root, args.run_id, request)
    except (ValueError, OSError) as error:
        print('镜像准备输入无效：'+safe_text(error), file=sys.stderr)
        return 2
    if args.plan:
        print(json.dumps({'status': 'not_run', 'request': request, 'evidence_parent': str(parent),
                          'docker': 'not_used', 'network': 'not_used'}, ensure_ascii=False))
        return 0
    try:
        code, result = execute(request, parent, args.run_id)
    except OSError as error:
        print('无法保存镜像准备证据：'+safe_text(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
