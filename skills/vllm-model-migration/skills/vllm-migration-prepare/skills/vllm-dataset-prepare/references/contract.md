# 请求与交接契约

统一参数：`--request`、`--work-root`、`--run-id`，可选 `--plan`。Python 3.10+，只用标准库。所有路径属于执行主机，相对 path 按 work-root 解析。

| 字段 | 含义与限制 |
| --- | --- |
| source | local：核验本地文件/目录；http：用户 HTTPS 来源；recipe：所选预置发布包 |
| recipe | gsm8k、ceval、mmlu、generic；generic 不可使用 source=recipe |
| layout | 可选 raw（默认）或 evalscope；evalscope 仅支持 ceval/gsm8k，在本次包外尝试中生成独立原生评测目录，不能同时指定 supporting_files |
| path | local 的实际数据目录/文件，或下载资产目录；下载和证据必须位于技能资源包外 |
| default_local | 可选 `{path, evidence:{path,sha256}}`，由 Agent 根据官方来源资料确认的默认本地数据目录或原始文件；仅用于 http/recipe，请求 path 不存在时先读取该候选。两个内部路径均须为规范绝对路径，evidence 固定已读官方资料字节；不能填未核实的共享缓存根 |
| url | http 必需；recipe 可显式替换来源。仅允许无凭据、查询参数或 fragment 的 HTTPS；重定向逐跳核验，在发送下一跳前拒绝降级 |
| split | 必填，预置配方必须属于其支持列表；选择清单只对应这个 split |
| subjects | 可选科目列表；适用于 C-Eval/MMLU；不减少加载器必需文件，也不改动原资料 |
| format | generic 必填 jsonl/json/csv；预置配方固定原格式，不允许更换 |
| required_fields | 通用样本必需字段；无表头通用 CSV 时也作为有序列名 |
| csv_header | 通用 CSV 是否有表头；默认 true；配方格式不受此字段更改 |
| files | 通用目录中明确的相对文件列表；省略按对应扩展名寻找。预置配方不能用它绕过布局核验 |
| selection | offset 非负整数，默认0；limit 可省略或为 null（offset 后全量），正整数最多 N 条；可选 file 为数据根内规范相对文件名，先筛此文件再选择。seed 仍须明确正整数 limit；记录原始行号，不裁剪题目 |
| supporting_files | 可选规范相对路径列表，用于 README/元数据等非样本文件；记录大小和 SHA256、rows=0，不参与选择，不能与显式 files 重复 |
| expected_sha256 | 下载 payload 的可选64位小写 SHA256；local 不接受这个来源声明 |
| expected_identity | `{format: vllm-dataset-file-list-v1, sha256: ...}`，比较所核验文件清单身份 |
| revision | 可选来源版本说明；它是声明，脚本不自动解析为已核对的 repo commit；local 不能伪装上游版本 |
| max_bytes | 下载及解包内容大小上限，默认512 MiB，最大16 GiB；超限保留部分成果并失败 |
| timeout_seconds | 每次网络阻塞等待上限，默认30秒，1—3600；不代表整个任务总时长 |

代理由实际下载进程的环境配置提供，不加入请求字段；判断和用户配置见[网络异常与代理](sources.md#网络异常与代理)。网络失败保留原始 failed 清单和已写出的部分资料，等待配置时不改为 completed/assets=pass。配置后创建独立尝试复核原来源与身份；中断后从新的 .part 重新下载，不承诺 Range 断点续传。

## 指定路径缺失时的来源选择

Agent 查阅官方来源后，若存在有依据的默认本地位置，将它及资料引用写入内部 default_local；没有该默认位置则省略。来源 URL、版本和默认位置是否属于本数据集由 Agent 依据实际资料判断，脚本校验证据字节与数据配方，不能仅凭文件夹名字声称上游身份已核实。客户 task.json 不增加字段，preparation.path 始终保持客户指定路径。

请求 path 已存在时保持原来源流程，不转向默认目录。请求 path 不存在且提供 default_local 时，先核对官方资料引用，再检查候选：明确不存在才使用原 HTTPS 下载器，在请求 path 下保存 payload、来源记录和解压数据；存在则按同一 recipe、split、subjects、selection 和 expected_identity 读取并验收。复用成功不创建请求 path、不下载、不改原文件，实际数据位置随准备回执交接。evalscope 布局仍在独立工作目录生成评测副本。

候选目录为空、缺文件、格式不支持、权限不足、目录不唯一或链接重定向时保留失败及具体候选路径，不当作“缺失”重新下载。这里只读取本任务支持的原始数据格式，不扫描全局 HF/ModelScope 缓存或自动转换 Arrow/Parquet 缓存。default_local 不能与 source=local 同用；旧 local 请求仍严格核验原路径。未提供 default_local 的 http/recipe 请求保持原下载行为。

默认本地文件不证明发布包摘要：请求声明 expected_sha256 且候选存在时阻塞，需由调用方明确核对文件清单 expected_identity，或不启用默认目录复用；不能静默忽略下载包身份要求。候选被复用时 source.kind=local、upstream_status=unverified、operation=reused；source.resolution 记录 requested_path、download_path、default_path、evidence、decision=reused_default、actual_path 和原文件清单 identity。下载分支 decision=download_requested。资料摘要与复用内容身份在成功发布前再次检查；下载失败或取消保留来源选择和原始失败证据，不自动重试。

输出目录必须避开候选数据位置和官方资料文件。--plan 只校验 default_local 形状与路径边界，不解引用资料、不检查候选存在性、不下载或创建目录。

## 文件与选择身份

预置配方检查加载器所需的全部 split。C-Eval/MMLU 可用科目从实际 dev 布局取得，要求其他配套 split 存在，明确列出科目范围；不把只有一个科目的本地子集声称为官方全量基准。

raw 布局保持既有行为：C-Eval 根目录已有的 `README.md` 作为目录元数据纳入文件清单；其 rows 为 0，split/subject 为 null，不参与样本选择，不生成或改写元数据。

evalscope 布局先核验原始文件，再在独立尝试的 evalscope-data 中复制所需 split 文件。C-Eval 按实际 CSV 科目生成 README 的 configs/data_files，声明本次评测 split 与 dev；全部原 CSV 仍保留并登记。features 依据评测 split 的真实表头声明字符串列，keep_default_na=false 保留空字符串，防止跨 split 列推断把 id 变为整数或补入空元数据；GSM8K 仅复制 train.jsonl/test.jsonl，不混入 socratic 变体，并生成 README，声明唯一 config_name=main、default=true 及 train/test 的精确文件映射。GSM8K 来源已有 README 不复制、不修改；派生 README 作为非样本文件纳入大小与 SHA256 核验，rows=0、split/subject=null。

原文件相对路径、字节与选择记录保持一致，复制前后核对摘要。输出 asset.path 指向派生目录；asset.provenance 保存布局版本、source_root、source_identity 与 source_files，asset.identity 记录最终目录身份。expected_identity 仍校验原始输入清单，不能拿加入元数据后的摘要冒充源身份。失败保留本次部分资产，不修改来源或覆盖旧尝试；准备成功仅表示文件验收通过，仍须原生数据预检。旧 GSM8K 派生目录缺少 main 元数据时，重新准备以取得新目录与回执，不回写旧目录、摘要或评测结果。

内容身份由排序后的相对路径、字节大小、SHA256 JSON 计算，样本选择身份由具体原文件相对路径、零基行号、split/科目计算。通用格式的行号表示数据记录位置，不包括 CSV 表头。seed 用于确定性选择，清单保存具体索引，跨环境必须消费同一记录而不是重新猜测随机结果。

准备请求的 limit 作用于筛选科目、split 并按文件顺序汇集后的候选记录，先 offset 再取上限；不自动变成每科上限。对接 EvalScope 时，每个任务只交接单一原文件，C-Eval/MMLU 按科目分别准备，复用完整数据目录并使用同一个用户 limit；对应 selection.count 为 `min(limit, 原文件行数)`，未限量时为原文件行数。评测的 sample_ids 从实际选择生成。省略和 null 均规范为无上限参数，不能将 seed/null 组合解释为随机全量。0、负数、布尔值、字符串和小数不合法。

URL 和 revision 声明相同且 payload、解包文件、路径与来源记录一致时复用；已有正确资料不重复下载。原始归档、部分下载及失败解包目录保留。修改解包文件后即使仍是合法格式也拒绝复用，不以新的内容摘要伪装原来源。

## 结果与退出码

包外 `<work-root>/<run-id>/vllm-dataset-prepare/<独立尝试>/` 保存 dataset-manifest.json、source-snapshot.json 和成功时的 sample-selection.json。结果沿用 schema_version、run_id、task_id、inputs、status、conclusions、evidence、uncovered，并记录资产文件、大小、摘要、行数、实际数据根、required_splits、选择记录和来源。失败时不保证选择文件存在，查看 evidence 所指文件及 diagnostic。

- 0：计划有效，或本次资产通过；不代表评分通过。
- 1：下载、解包、格式、身份或证据执行失败，保留 failed 清单和部分资产；磁盘不可写时不能保证清单落盘。
- 2：输入/证据路径无效，运行前拒绝，不创建运行文件。

证据目录不得与资产或完整总 Skill 根重叠，运行路径符号链接重定向拒绝；保护范围不依赖插件标记。计划模式不访问数据、联网或写文件。
