"""客户入口适配；自然语言理解与外部资料复核属于 Agent。"""
import json
from pathlib import Path
import sys

from coordination.cli import read_request
from coordination.request import output
from .contract import FILES, normalize, validate
from .defaults.policy import pending


def plan(task, parent):
    from .quality.conclusion import summarize
    from .alignment_summary import summarize as alignment_summary
    result = {'schema_version': '1.0', 'task_id': 'vllm-model-migration', 'status': 'not_run',
            'success': False, 'task_quality_conclusion': summarize('not_run', [], {}, None),
            'inputs': task, 'evidence_parent': str(parent),
            'deliverables': list(FILES) if task['accuracy']['evaluator'] == 'evalscope' else [],
            'delivery_supported': task['accuracy']['evaluator'] == 'evalscope',
            'next_action': 'agent_resolve_intent_and_observations',
            'uncovered': ['仅校验客户字段；未解析版本、查找资产、占用设备或完成任何评测',
                          '仅为 JSON 静态预览，尚未合成 prompt；后端与默认参数不代表最终执行选择',
                          *['待运行时解析默认字段：' + field for field in pending(task)]]}
    result['alignment_summary'] = alignment_summary(result, task['accuracy'], intent_known=False)
    return result


def main(args):
    try:
        source = args.task.expanduser().absolute()
        raw = read_request(source)
        validate(raw)
        parent = output(args.work_root, args.run_id).parent / 'customer-workflow'
        if args.action != 'run':
            raise ValueError('客户 --task 仅用于 run；历史分析使用原 --request')
        if args.plan:
            print(json.dumps(plan(normalize(raw), parent), ensure_ascii=False))
            return 0
        from .runner import run
        code, result = run(args, source, raw, parent)
        print(json.dumps(result, ensure_ascii=False))
        return code
    except ValueError as error:
        print(json.dumps({'status': 'invalid_input', 'reason': str(error)}, ensure_ascii=False))
        return 2
    except (OSError, KeyError, TypeError, RuntimeError, RecursionError):
        print('客户流程输入或证据不可读；未确认交付成功。', file=sys.stderr)
        return 1
