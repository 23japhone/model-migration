"""导出组装：只读核验及一次整体发布，不部署、不评分、不调优。"""
import json
from pathlib import Path
import sys

from coordination.cli import Parser, read_request
from coordination.runtime import sources
from . import accuracy, contracts, deployment, provenance, publication, request, flat_publication


def configurations(value):
    configs = {key: sources.bound(value[key]) for key in request.FILES}
    for key, check in (('benchmark_plan', contracts.benchmark), ('accuracy_plan', contracts.accuracy)):
        check(configs[key])
    contracts.deployment(configs['deployment'], runtime_paths='runtime_environment' in value)
    return configs


def validated(value):
    configs = configurations(value)
    _, original, handoff, baselines = provenance.load(value['entry_manifest'])
    deployment.validate(configs['deployment'], configs['benchmark_plan'], configs['accuracy_plan'],
                        original, handoff, value.get('runtime_environment'))
    accuracy.validate(configs['accuracy_plan'], baselines, handoff)
    return configs


def arguments(argv):
    parser = Parser(description='完整迁移基线的三 JSON 交接；不执行性能调优')
    parser.add_argument('--request', type=Path, required=True, help='包外导出请求')
    parser.add_argument('--work-root', type=Path, required=True, help='包外交接及证据根')
    parser.add_argument('--run-id', required=True, help='本次交接标识；每次调用建立独立尝试')
    parser.add_argument('--plan', action='store_true', help='只读顶层请求，不读取引用或写文件')
    parser.add_argument('--layout', choices=('legacy', 'flat'), default='legacy', help='旧兼容目录或客户同级目录')
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    try:
        value = request.validate(read_request(args.request.expanduser().absolute()))
        parent = request.parent(args.work_root, args.run_id)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, RecursionError):
        print('导出输入无效：请核对顶层契约、引用及输出安装边界。', file=sys.stderr)
        return 2
    if args.plan:
        planned = request.plan(value, parent)
        if args.layout == 'flat':
            planned['deliverables'] = list(flat_publication.FILES.values())
        print(json.dumps(planned, ensure_ascii=False))
        return 0
    try:
        source = sources.ref(args.request.expanduser().absolute())
        sources.protect(parent, [Path(source['path'])])
        configs = validated(value)

        def recheck():
            contracts.require(sources.bound(source) == value, '导出请求在校验后发生变化')
            contracts.require(validated(value) == configs, '候选配置在校验后发生变化')

        try:
            if args.layout == 'flat':
                result = flat_publication.publish(value, configs, parent.parent, args.run_id, recheck)
            else:
                result = publication.publish(value, configs, parent, args.run_id, recheck)
        except OSError:
            print(json.dumps({'status': 'failed', 'reason': '交接写入失败；未完成正式交付'}, ensure_ascii=False))
            return 1
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        result = {'status': 'failed', 'reason': '交接取消；请检查内部失败回执，原始基线未改写'}
        code = 1
    except sources.OutputError:
        result = {'status': 'invalid_input', 'reason': '交接输出与来源、资产或安装边界重叠'}
        code = 2
    except ValueError as error:
        result = {'status': 'blocked', 'reason': str(error)}
        code = 3
    except (OSError, TypeError, KeyError, RuntimeError, RecursionError):
        result = {'status': 'blocked', 'reason': '来源不完整、类型无效或证据不可读；未完成交接'}
        code = 3
    print(json.dumps(result, ensure_ascii=False))
    return code
