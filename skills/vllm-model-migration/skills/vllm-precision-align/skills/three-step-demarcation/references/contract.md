# 三步定界请求、结果与资源契约

本 Skill 把一条完整异常 Chat 请求（badcase）在异常发生的服务上按“渲染 prompt →
反解 prompt → 原始生成”三步定界到九类结论之一。只调用只读推理接口，不重启服务、
不改启动参数、不动生产流量。脚本自身仅用 Python 3.10+ 标准库。

## 输入

统一参数 `--request --work-root --run-id --action run|analyze --plan`，默认 run。

| 字段 | 契约 |
| --- | --- |
| schema_version | 固定 1.0。 |
| service.endpoint | 目标（NPU）服务地址，`http(s)://host:port`，带不带 `/v1` 均可；tokenize/detokenize 与 /version 在根路径，其余在 `/v1`。 |
| service.model | 可选 served-model-name；省略则 `GET /v1/models` 自动发现首个模型。 |
| badcase | 完整原始 chat 请求体引用 `{path,sha256}`，须为含 `messages` 的对象；请求原样使用不简化。 |
| render_mode | `auto`（默认，render 端点 → tokenize 逐级降级）、`render`、`tokenize`。`local`（本地 tokenizer 渲染）需 transformers，与标准库约束冲突，**不支持**（K16）。 |
| max_tokens / temperature | 可选；默认取 badcase 中的值，再默认 512 / 0。生成长度须覆盖异常位置。 |
| timeout_seconds | 可选，默认 60，1—86400。 |

所有引用为规范绝对路径及 64 位小写 SHA256，拒绝符号链接重定向、重复 JSON 键、
非有限数字和未知顶层字段。请求不得包含凭据。

`--plan` 只检查顶层形状、引用写法与包外输出边界，不读取 badcase 内容、不访问
服务、不写运行目录。

## 输出与退出码

包外 `work-root/run-id/three-step-demarcation/UUID/` 含：

- `demarcation-manifest.json`：共同 `schema_version/run_id/task_id/inputs/status/
  success/conclusions/evidence/uncovered`，另有 `detailed_report` 与 `operation`。
- `rendered.json` / `rendered_prompt.json` / `raw_completion.json` / `chat_response.json`
  四份证据（fatal REQUEST 时步骤 2/3 未执行，不产生这些文件）。

`conclusions.demarcation`：`pass` 表示得到确定性九类结论（非 INCONCLUSIVE）；
`review_required` 表示证据不足结论为 INCONCLUSIVE。`success` = 状态 completed 且
该结论为 pass。

`detailed_report` 含 `meta`（服务/模型/渲染途径/版本与契约/请求次数/证据索引）、
`step1_request`、`step2_render`、`step3_raw`、`step3_chat`、`has_tools`、`verdict`、
`primary`、`advice`。`uncovered` 列出证据齐全度缺口（K4）。

退出码：`0` 定界完成且结论确定；`1` 执行/证据失败或结论为 INCONCLUSIVE；
`2` 请求或输出边界无效；`3` 必需输入不足。不能只凭退出码判断结论。

## 请求预算与不重发

一次定界固定请求预算为 **10 次**（版本探测、模型发现、渲染/反解、原始生成、chat
对照，及版本未知时的能力探测）。预算由 `runner.REQUEST_BUDGET` 预写，超限即失败，
**不自动重发、不靠重试放大**。每次请求写入台账（`Client.ledger`）供对账。

服务不支持保留特殊 token 的扩展字段（`skip_special_tokens=false` 等）时，第 3 步
原始生成请求失败**不重发**，如实记录失败并将结论标为 INCONCLUSIVE（特殊 token
可能被过滤，think/tool 标记检测置信度受限）。

## 版本兼容（L1/L2/L3）

- L1：`GET /version` 探测版本并解析 `X.Y.Z`（容忍 rc/dev 后缀）。
- L2：按版本契约分支各决策点（tokenize/detokenize 路径与字段、render 响应形态、
  render 是否挂载、tokenize 请求字段、completions 扩展字段）。
- L3：版本未知或非 vLLM 服务时用最小只读请求探测路径与字段名兜底，不影响定界。

## 适用边界（明确不做）

- **本地渲染（K16）**：需 transformers，不支持。
- **图片/超长请求（K17）**：messages content 仅支持 str；多模态或超长请求不适用，
  不硬凑结论。
- **根因验证**：九类结论用于选择排查方向，根因另行验证（本 Skill 不进入 msprobe
  逐层打点或修复）。

## 结论与证据要求

九类结论、判定口径与证据要求见 `references/decision-tree.md`、`step1-request-check.md`、
`step2-render-verify.md`、`step3-raw-parse.md`；建议话术与乱码阈值见 `references/profiles.json`。
