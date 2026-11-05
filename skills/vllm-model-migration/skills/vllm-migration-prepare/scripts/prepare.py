#!/usr/bin/env python3
"""编排 vLLM-Ascend 准备任务，核对资产回执并汇总交接。"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
from migration_prepare.dispatch import plan_tasks, run_task
from migration_prepare.results import EvidenceWriteError, begin_attempt, finish, output_parent, safe_text, save
from migration_prepare.request import normalize_request
from migration_prepare.review import review_receipt, review_versions


class ChineseArgumentParser(argparse.ArgumentParser):
    """显示中文帮助及输入错误，保留技术参数标识。"""
    def format_help(self):
        return super().format_help().replace('usage: ', '用法：', 1).replace('options:\n', '参数：\n', 1)

    def error(self, message):
        self.exit(2, '命令行参数无效，请使用 --help 核对必需参数与取值。\n')


def execute(request, tasks, parent, root, run_id, base):
    attempt, manifest = begin_attempt(parent, request, run_id)
    manifest['tasks'] = tasks
    save(attempt, manifest)
    for name, item in tasks.items():
        if item['status'] == 'blocked':
            continue
        item['status'] = 'running'
        save(attempt, manifest)
        try:
            result = run_task(name, request['tasks'][name], item, attempt, root, run_id)
            item.update(review_receipt(name, item, result, root, run_id))
        except EvidenceWriteError:
            raise
        except KeyboardInterrupt:
            manifest['cancelled'] = True
            item.update(status='failed', diagnostic={'type': 'KeyboardInterrupt', 'message': '用户取消了本次准备'})
            save(attempt, manifest)
            break
        except (ValueError, OSError, TypeError, KeyError, subprocess.TimeoutExpired) as error:
            item.update(status='failed', conclusions={'assets': 'fail'}, diagnostic={
                'type': type(error).__name__, 'message': '子任务执行或回执复核失败：'+safe_text(error)})
        save(attempt, manifest)
    conclusion, records, reason = review_versions(request, base, attempt)
    manifest['conclusions']['version_materials'] = conclusion
    manifest['version_review'] = records
    if reason:
        manifest['uncovered'].append(reason)
    for name, item in tasks.items():
        manifest['uncovered'].extend(name+'：'+x for x in item.get('uncovered', []))
        if not item['required'] and item['status'] != 'completed':
            manifest['uncovered'].append(name+' 为可选任务，本次未完成；不能交付该资产')
    return finish(attempt, manifest)


def main(argv=None):
    parser = ChineseArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('-h', '--help', action='help', help='显示帮助并退出')
    parser.add_argument('--request', required=True, type=Path, help='阶段请求 JSON；任务请求路径相对该文件解析')
    parser.add_argument('--work-root', required=True, type=Path, help='包外证据根，资产与运行目录必须分开')
    parser.add_argument('--run-id', required=True, help='阶段及三个任务共用的运行标识')
    parser.add_argument('--plan', action='store_true', help='委托子任务核验输入，不下载或写运行文件')
    args = parser.parse_args(argv)
    try:
        base = args.request.expanduser().resolve().parent
        root = args.work_root.expanduser().resolve()
        request = normalize_request(json.loads(args.request.read_text(encoding='utf-8-sig')), base)
        parent = output_parent(root, args.run_id)
        tasks = plan_tasks(request, root, args.run_id)
        if args.plan:
            print(json.dumps({'status': 'not_run', 'purpose': request['purpose'], 'tasks': tasks,
                              'evidence_parent': str(parent), 'version_review': 'not_evaluated'}, ensure_ascii=False))
            return 2 if any(t['required'] and t['status'] == 'blocked' for t in tasks.values()) else 0
        code, result = execute(request, tasks, parent, root, args.run_id, base)
        print(json.dumps(result, ensure_ascii=False))
        return code
    except (ValueError, OSError) as error:
        print('阶段输入或证据留存失败：'+safe_text(error), file=sys.stderr)
        return 2 if isinstance(error, ValueError) else 1


if __name__ == '__main__':
    raise SystemExit(main())
