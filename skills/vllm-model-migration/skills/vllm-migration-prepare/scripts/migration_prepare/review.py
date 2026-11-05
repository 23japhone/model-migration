"""复核交接契约与资料记录，不重复子任务的下载、内容或镜像检查。"""
from datetime import date
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from .results import safe_text, sanitized, write_json
from .asset_contract import review_asset

CONCLUSIONS = {'pass', 'fail', 'not_evaluated', 'review_required'}


def shape(value):
    if not isinstance(value, dict) or value.get('schema_version') != '1.0':
        raise ValueError('子任务清单缺少有效 schema_version')
    for field, kind in [('run_id', str), ('task_id', str), ('inputs', dict), ('conclusions', dict), ('evidence', dict), ('uncovered', list)]:
        if not isinstance(value.get(field), kind):
            raise ValueError('子任务清单字段类型无效：'+field)
    if not value['conclusions'] or any(v not in CONCLUSIONS for v in value['conclusions'].values()):
        raise ValueError('子任务结论枚举无效')
    if any(not isinstance(x, str) for x in value['uncovered']) or any(not isinstance(x, str) for x in value['evidence'].values()):
        raise ValueError('子任务证据或未覆盖项无效')


def review_receipt(name, item, process, root, run_id):
    summary = json.loads(process.stdout)
    if not isinstance(summary, dict) or not isinstance(summary.get('manifest'), str):
        raise ValueError('子任务没有返回清单位置')
    path = Path(summary['manifest'])
    parent = Path(root).resolve()/run_id/f'vllm-{name}-prepare'
    if not path.is_absolute() or path.resolve() != path or not path.is_relative_to(parent) or len(path.relative_to(parent).parts) != 2:
        raise ValueError('子任务清单位置不属于本次运行的独立尝试')
    raw = path.read_bytes()
    value = json.loads(raw)
    shape(value)
    status = review_consistency(name, value, summary, process.returncode, item['request'], run_id)
    if value['evidence'].get('manifest') != str(path):
        raise ValueError('子任务清单没有自证据位置')
    if status == 'completed':
        review_success(name, value, path.parent)
    return {'status': status, 'conclusions': value['conclusions'], 'manifest': str(path),
            'manifest_sha256': hashlib.sha256(raw).hexdigest(), 'asset': value.get('asset'),
            'uncovered': value['uncovered'], 'host': value.get('host'), 'diagnostic': value.get('diagnostic')}


def review_consistency(name, value, summary, code, request, run_id):
    if value['run_id'] != run_id or value['task_id'] != f'vllm-{name}-prepare' or value['inputs'] != request:
        raise ValueError('子任务的运行、任务或输入身份与本次请求不一致')
    status = value.get('status')
    expected = {0: 'completed', 1: 'failed', 3: 'blocked'}.get(code)
    if expected is None or status != expected or summary.get('status') != status or summary.get('conclusions') != value['conclusions']:
        raise ValueError('子任务退出码、摘要、执行状态和清单结论不一致')
    return status


def review_success(name, value, attempt):
    if value['conclusions'].get('assets') != 'pass' or not isinstance(value.get('asset'), dict):
        raise ValueError('子任务完成但没有资产通过结论或资产记录')
    for evidence in value['evidence'].values():
        path = Path(evidence)
        if not path.is_absolute() or path.resolve() != path or not path.is_relative_to(attempt) or not path.is_file():
            raise ValueError('子任务成功证据缺失、越界或通过符号链接重定向')
    review_asset(name, value['asset'], value['inputs'])


def official_url(value):
    parsed = urlsplit(value) if isinstance(value, str) else None
    if parsed is None or parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('版本资料需要无凭据的官方 HTTPS 链接')
    host = parsed.hostname
    valid = host == 'docs.vllm.ai' or host == 'www.hiascend.com' or host == 'hiascend.com'
    valid |= host == 'github.com' and parsed.path.startswith(('/vllm-project/vllm/', '/vllm-project/vllm-ascend/'))
    if not valid:
        raise ValueError('版本资料来源不属于 vLLM、vLLM-Ascend 或昇腾官方资料')


def material(record, base):
    fields = {'topic', 'url', 'checked_at', 'evidence_file', 'evidence_sha256', 'conclusion', 'note'}
    if not isinstance(record, dict) or set(record) != fields or record['topic'] not in ('version_pair', 'hardware', 'software_stack'):
        raise ValueError('版本复核记录必须明确主题、来源、日期、证据、摘要、结论及说明')
    if record['conclusion'] not in ('pass', 'fail', 'review_required') or not isinstance(record['note'], str) or not record['note'].strip():
        raise ValueError('资料结论和复核说明无效')
    official_url(record['url'])
    checked = date.fromisoformat(record['checked_at'])
    if checked > date.today():
        raise ValueError('资料复核日期不能在未来')
    path = (base/Path(record['evidence_file']).expanduser()).resolve()
    if not path.is_file() or path.stat().st_size > 4*1024**2:
        raise ValueError('版本资料证据必须是可读且不超过 4 MiB 的文件')
    raw = path.read_bytes()
    raw.decode('utf-8-sig')
    if hashlib.sha256(raw).hexdigest() != record['evidence_sha256']:
        raise ValueError('版本资料的文件摘要与复核记录冲突')
    return sanitized(record), safe_text(raw.decode('utf-8-sig'))


def review_versions(request, base, attempt):
    records, errors = [], []
    for index, record in enumerate(request['version_review']):
        try:
            checked, content = material(record, base)
        except (ValueError, OSError, TypeError) as error:
            errors.append(f'记录 {index} 未通过复核：'+safe_text(error))
            continue
        frozen = attempt/f'version-{index}.json'
        # 必需证据写入失败向上报告，不能当作资料待复核而掩盖。
        write_json(frozen, {'record': checked, 'content': content})
        records.append({**checked, 'snapshot': str(frozen)})
    conclusion, reason = version_conclusion(request, records, errors)
    return conclusion, records, reason


def version_conclusion(request, records, errors):
    required = request['purpose'] != 'assets' or request['tasks'].get('image', {}).get('required', False)
    topics = {r['topic'] for r in records}
    if any(r['conclusion'] == 'fail' for r in records):
        return 'fail', '；'.join(['版本资料存在明确冲突', *errors])
    if errors:
        return 'review_required', '；'.join(errors)
    if required and ('target' not in request or topics != {'version_pair', 'hardware', 'software_stack'}):
        return 'review_required', '缺少 target 或版本搭配、硬件、软件栈的必需复核记录'
    if records and 'target' not in request:
        return 'review_required', '资料复核必须关联明确的 target'
    if any(r['conclusion'] != 'pass' for r in records):
        return 'review_required', '版本资料仍需人工复核'
    return ('pass' if records else 'not_evaluated'), None
