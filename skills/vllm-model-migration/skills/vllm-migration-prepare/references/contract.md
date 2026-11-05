# 阶段请求与交接契约

Python 3.10+；阶段仅依赖标准库。三个任务入口由总 Skill 布局索引固定定位到本阶段嵌套任务的 scripts/prepare.py，不接受任意命令、shell、SSH 地址或凭据。共享 Schema 与模板归总入口静态资源维护，无额外可调用技能。

客户模式另有 scripts/customer.py 公开辅助入口，负责镜像身份解析、每次调用原版脚本准备共用 EvalScope，以及 AISBench/资产 SDK 的隔离准备。字段、日志、超时、预览和续接见[安装契约](../../../references/evalscope-installation.md)及[客户观察契约](../../../references/customer-observations.md)。不改变下表旧阶段请求的安装边界。

| 字段 | 用途和规则 |
| --- | --- |
| purpose | assets、precheck、deployment 或 deployment_precision；默认 assets |
| tasks | model/image/dataset 条目；至少一个必需任务，不识别的任务名拒绝 |
| tasks.*.request | 对应任务 JSON 路径，相对阶段请求文件解析；任务资产相对 work-root 解析 |
| tasks.*.required | 可选，默认 true；purpose 强制的任务始终必需 |
| tasks.*.python | 可选，已安装 Python 解释器绝对路径，适用于官方 SDK 的独立环境；不安装依赖 |
| tasks.*.timeout_seconds | 单个子任务最大等待秒数，默认 3600，范围 1—86400；前置计划最多等待 60 秒 |
| target | 镜像或版本资料使用时必需：backend=vllm-ascend、vllm、vllm_ascend、platform 和 hardware{name,family}；不推断默认版本 |
| version_review | 资料记录列表：topic、url、checked_at、evidence_file、evidence_sha256、conclusion、note；说明用中文、专有名词保持原名 |

version_review 的 topic 为 version_pair、hardware、software_stack。涉及镜像或 purpose 非 assets 时，三项均必需。url 必须为 vLLM、vLLM-Ascend 或昇腾官方 HTTPS 资料，无凭据或查询参数。evidence_file 相对阶段请求文件解析，是不超过 4 MiB 的 UTF-8 资料；evidence_sha256 固定原文件。checked_at 为 YYYY-MM-DD，conclusion 为 pass/fail/review_required。调用方必须实际阅读并核对目标，不能把未阅读的链接记为 pass。脚本保存脱敏文本副本与原始摘要，原文件应保留。

阶段先运行各任务 --plan 得到规范化输入，冻结 JSON 后实际执行；核对任务 run_id、task_id、inputs、退出码、摘要和清单结论一致性、成功证据完整性、资产位置及内容身份。它不重新扫描权重、数据或 Docker 镜像；子任务清单 SHA256 标识本次回执，后续资产变化需重新调用任务核验。

成功回执还须符合本次资产声明：模型 path、scope、task、modalities 及 expected_identity；镜像 image ID、repository digest、platform、recipe、hardware 和完整 daemon 位置；数据集 path、recipe、format、expected_identity、公开配方的 required_splits、文件声明及 split/subjects/selection。通用单文件的数据集 path 可以是请求文件的父目录，但文件声明必须唯一对应请求文件；解包后的数据目录必须在请求资产目录内。样本位置与文件声明、样本数量和选择摘要须一致。这些检查只复核公开 JSON 声明，内容核验仍由任务负责。

畸形计划只阻塞所属任务，畸形成功回执只使所属任务失败。版本资料逐条核验，坏记录保留带序号的原因，继续其余记录；任意有效记录明确 fail 时，最终 version_materials 始终 fail，其他记录缺失或损坏不能把它降为 review_required。

任务状态即时保存，必需请求缺失或计划无效仅阻塞该任务，独立任务继续。可选任务失败不阻止必需范围完成，其资产不可交付为成功。任务输入路径不能与本次运行目录或其他资产重叠。包外输出拒绝运行路径符号链接重定向，无条件保护完整总 Skill 根。单一写入者串行维护台账，不承诺并发调度、自动断点恢复或 exactly-once。

阶段及任务的 `resources` 仍为相对完整总 Skill 根的路径到实际 SHA256 映射，例如 `references/task-result.schema.json`；按总入口静态资源契约枚举当前普通文件，根资源与各级 Skill 的直属资源不重复。无需构建资源清单，新增和修改资源自动反映，缺必需资源明确阻塞其消费者；独立就绪任务继续。旧回执保留原始资源键和摘要，不改写历史文件，也不把资源摘要当作资产或运行通过证明。

每次输出为 `<work-root>/<run-id>/vllm-migration-prepare/<独立尝试>/`：

- prepare-manifest.json：共同结果外层、阶段目的和输入身份、host、阶段及共同静态资源摘要、任务状态、资产与版本资料结论、未覆盖项。
- task-ledger.json：每个任务的必需性、规范化输入和摘要、状态、回执位置/摘要、完整资源摘要、诊断和子任务结论。
- prepare-handoff.json：目标、各项资产与其清单、版本复核记录和未覆盖项。
- *-request.json / *-process.json：冻结请求和脱敏进程证据；子任务证据仍归各任务目录。
- version-*.json：已复核资料的脱敏快照；保存原资料 SHA256，不把快照摘要冒充原资料摘要。

实际执行退出码：0 表示本次必需准备完成；1 表示必需任务失败、回执无效、明确资料冲突、取消或证据写入失败；2 表示顶层输入或输出路径无效，或 --plan 中有必需任务阻塞；3 表示实际执行后的必需任务/版本资料阻塞。标准输出成功留存时给出 manifest/status/conclusions；磁盘不可写无法保证完整清单，标准错误提示限制，不制造成功结果。

超时和取消保留已回收的 stdout/stderr、诊断与子进程 PID。POSIX 环境终止本次新建的 CLI 进程组；其他环境终止直接子进程，跨平台后代进程行为需另行验证。不会终止 Docker daemon，已提交给 daemon 的 pull/load 可能仍在执行，重试前须复查。冻结请求、进程证据、台账、最终清单或资料快照无法保存时，停止后续操作；最后发布终态，未完整留存证据不宣告完成。单个 JSON 原子替换不代表多个文件具有事务性，异常后以阶段清单和原始证据复核状态。

计划模式不创建目录或冻结请求，不下载或执行任务实际操作；版本记录在实际执行末尾复核，计划不产生资料通过结论。更改代理等环境后重新执行创建新尝试，不覆盖旧证据。取消结束当前阶段，剩余任务 not_run；重新运行仍委托任务自身的复用能力。
