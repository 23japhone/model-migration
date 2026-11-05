# 模型准备请求与交接契约

版本：`1.0`。运行需要 Python 3.10+；本地准备与计划模式仅用标准库。脚本在资产所在环境执行，实际主机名和资产路径进入结果，不提供 SSH 参数或服务启动入口。

技能说明和资源路径不绑定具体业务项目或调用工具。调用方保留完整总 Skill 资源，提供实际请求文件和执行环境，使用同一命令行入口执行；无需依赖某一智能体的专用环境变量。完整总 Skill 根始终纳入运行证据的禁止写入范围，不依赖插件标记。

技能元数据、工作流程、属性说明、脚本帮助和诊断中的普通说明使用中文。专有名词、技术术语保持原名：原名为英文就使用英文，原名为中文就使用中文，例如 vLLM-Ascend、tokenizer、processor、Safetensors、Hugging Face、ModelScope 和华为昇腾。技能名称、参数名、JSON 字段、枚举值、格式标识和官方 API 名称保留原值；外部 SDK 或系统异常的原文可保留供排查，不改写历史原始证据。

## 客户来源发现

`scripts/resolve.py --model` 读取客户 model 对象，发现完整本地权重时返回带 expected_identity 的本地准备请求和 asset；仓库 ID 为显式字符串时，resolution 为 local_identity_review_required，否则为 local_weights_verified。stdout 的 status=resolved 仅表示发现完成，不证明与上游仓库身份一致。客户流程须按[模型身份复核](../../../../../references/customer-observations.md#模型身份复核)核对仓库和本地内容后才能复用；不能凭完整性或名称跳过冲突。没有完整本地模型时保持 ModelScope、Hugging Face 的有区别查询和版本冻结，不下载。以下 prepare.py 独立请求及本地来源的上游未验证边界保持不变。

## 请求字段

| 字段 | 要求与默认值 |
| --- | --- |
| `source` | `local`、`huggingface`、`modelscope` |
| `path` | 本地资产目录或下载目标；相对路径基于明确的 `--work-root` 解析 |
| `scope` | `metadata` 或 `full`；默认 `full` |
| `repository` | 远程来源必需；`namespace/name` 或对应官方模型主页 HTTPS 链接；revision 单独传入 |
| `revision` | HF 默认显式请求 `main`，解析为实际 repo commit；MS 默认显式请求 `master`，保留分支/标签及接口返回的逐文件 revision，接口未提供时记录缺口 |
| `expected_snapshot` | 可选远程文件清单完整 SHA256，客户 resolver 首次发现后固定；后续仓库清单变化在下载前拒绝，不重新解释分支为新版本。本地来源不接受此字段 |
| `task` | `full` 必需：`chat`、`generate`、`embedding`、`rerank`；非 chat 不要求聊天模板 |
| `modalities` | 默认 `["text"]`；可指定 `image`、`video`、`audio`，有非文本模态时要求 processor 资料。核验文件存在与 JSON 结构，不证明对应模态可运行 |
| `required_files` | 额外必需的相对路径列表；metadata 不能要求权重文件 |
| `expected_files` | 可选 `{ "文件路径": {"size": 整数, "sha256": "64位小写摘要"} }`；给出字段必须匹配 |
| `expected_identity` | 可选 `{ "format": "vllm-model-file-list-v1", "sha256": "64位小写摘要" }`；与本次范围的文件清单比较 |
| `max_workers` | 远程下载线程数，默认 2，范围 1–32 |

未知字段拒绝；本地请求不接受 `repository/revision` 冒充已验证上游身份。请求不接受密码、token 或其他凭据字段。官方 SDK 使用授权环境/已有认证渠道；本技能不写认证配置。

网络代理属于实际下载进程的环境配置，不新增模型请求字段。确认代理问题后先询问用户是否有可用代理；有则请用户提供并在实际下载进程使用，无则停止 HF 下载，按[无代理时改用 ModelScope](sources.md#无代理时改用-modelscope)生成独立来源请求。判断与恢复规则见[网络异常与代理](sources.md#网络异常与代理)。保留原始 failed 清单，来源切换不改写旧结论；新来源只按自身 repository、revision、目录和内容身份验收，不将其成功记为 HF 通过。

## 资产核验

- 配置根必须为 JSON 对象，含可识别的 `architectures` 或 `model_type`；保留嵌套配置，未知架构留给后续预检。
- 元数据范围获取已知配置、模板与索引，不核对实际分片；可通过 `required_files` 明确额外的非权重资料。
- 完整模型范围核对常见 Transformers tokenizer 文件；快速 tokenizer JSON 的类型限定为 BPE、WordPiece、WordLevel、Unigram 并检查基本 vocab，BPE 还要求 merges 列表；不执行 tokenizer 加载。`chat` 任务必须有非空模板。权重索引必须引用相应格式的相对路径分片，不能用普通配置冒充权重。
- Safetensors 核对头部长度、数据偏移、已识别 dtype 的大小与形状、分片内索引张量；未识别 dtype 明确拒绝。`.bin` 只核对文件、索引及提供的内容校验值，不调用 pickle 或 torch。
- HF 官方缓存的正规 `models--*/snapshots/<commit>` 可读取相同缓存中 `blobs/` 下的文件链接；其他目录外链接与目录符号链接拒绝。
- 忽略 `.git`、Python 缓存、官方 SDK 元数据 `.msc/.mv/.mdl`、SDK 临时目录和 `.cache/huggingface`、`.cache/modelscope`。它们不成为模型内容身份的一部分。
- 未提供上游摘要的本地资产只建立内容身份与所述结构证据，不声称已与发布源逐字节一致。

## 文件身份

`vllm-model-file-list-v1`：按相对 POSIX 路径排序，将每个文件的 `path/size/sha256` 组成数组，使用 UTF-8、`ensure_ascii=False`、排序 JSON 字段名、无多余空格序列化，再计算 SHA256。作用范围由 `asset.scope` 指定，不能把其他工具同名或近似摘要直接拿来比较。

扫描以流式方式计算 SHA256 和 HF 非 LFS 文件的 Git blob SHA1；扫描和配置/头部核验期间复查文件身份、大小和时间信息，发现变化不交付稳定身份。这不是跨进程锁或未来文件不变的保证，消费清单前仍应重新核验。

## 输出与退出码

标准输出是一条 JSON，含 `manifest/status/conclusions`。清单位于：

```text
<work-root>/<run-id>/vllm-model-prepare/<attempt-id>/
├── model-manifest.json
└── source-snapshot.json     # 成功解析远程来源后保留；本地请求无此文件
```

共同外层使用[组级结果 Schema](../../../../../references/task-result.schema.json)。`asset` 含实际路径、范围、执行操作、文件清单、内容身份、配置资料、权重检查及来源。敏感字段和值在结果中脱敏，资产摘要基于原文件计算。

| 退出码 | 含义 |
| --- | --- |
| `0` | 计划模式输入有效但未执行；或实际准备 `completed/assets=pass` |
| `1` | 下载、核验、取消或证据留存失败；如可写入则留存 failed 清单 |
| `2` | 请求、run_id 或输出路径无效，开始执行前拒绝，无运行目录 |
| `3` | 官方 SDK 缺失或其导入依赖缺失，留存 blocked 清单 |

不能只看退出码 0：必须区分计划和执行、元数据和完整模型范围。`runtime_support` 不参与本任务通过判定，始终不评估。`work-root` 不得位于模型、技能资源或实际插件安装根内；每次尝试使用新目录，保留既有记录及下载残留。
