---
name: three-step-demarcation
description: 服务化解析链路三步法定界：当 /v1/chat/completions 返回异常（tool call 不返回、reasoning_content 缺失、输出被吞、乱码/无意义重复）时，用同一组 token_ids 把链路拆成「渲染 prompt → 反解 prompt → 模型原始生成」，报告主要定界标签（REQUEST / CHAT_TEMPLATE / MODEL_NUMERIC / MODEL_INCOMPLETE / TOOL_PARSER / THINKING_PARSER / SERVICE_OUTPUT / MODEL_OUTPUT / INCONCLUSIVE / PASS）。用于精度问题分析的问题定界阶段，也可独立使用。
---

# three-step-demarcation —— 服务化解析链路三步定界

当 `/v1/chat/completions` 返回异常时，不要只凭 Chat API 最终响应猜测是 parser
还是模型精度问题。三步法把链路拆成「渲染 prompt → 反解 prompt → 模型原始生成」，
在异常发生的服务上复用**同一组 token IDs** 定界。与 `logprobs-diff` 互补：本 skill
看**结构与解析**，logprobs-diff 看**数值分布**。

## 使用前提

- 已完成基础自检（权重、环境、配置、后处理、测试方法）且未发现可闭环问题；
- 已有一个**稳定复现的完整 Chat 请求**（badcase.json），保留全部原始字段；
- 已知服务地址、端口和实际 served-model-name（模型名可自动发现）；
- 只调用只读推理接口，不重启服务、不改启动参数、不动生产流量。

## 用法

```bash
python3 scripts/demarcation.py \
    --request /包外/request.json --work-root /包外/runs --run-id demarcation-001
# 只检查形状与输出边界
python3 scripts/demarcation.py --request /包外/request.json \
    --work-root /包外/runs --run-id demarcation-001 --plan
# 拿旧结果重算结论，不重发请求
python3 scripts/demarcation.py --action analyze --request /包外/analyze.json \
    --work-root /包外/runs --run-id demarcation-002
```

输入/结果/退出码/预算/版本兼容/边界见 [references/contract.md](references/contract.md)。
示例请求见 [assets/request.example.json](assets/request.example.json)，示例路径与
SHA256 必须替换，不能作为运行证据。

### 输入 request.json

```json
{
  "schema_version": "1.0",
  "service": {"endpoint": "http://127.0.0.1:8000", "model": null},
  "badcase": {"path": "/abs/badcase.json", "sha256": "<64位小写SHA256>"},
  "render_mode": "auto",
  "max_tokens": 512,
  "temperature": 0,
  "timeout_seconds": 60
}
```

`badcase` 指向完整原始 chat 请求体（messages / tools / tool_choice /
chat_template_kwargs / 采样参数原样保留）。

## 三步法定界流程

```
第 1 步 准备原始请求(静态校验) ──异常──▶ REQUEST
        │ 正常(请求原样使用, 不简化)
        ▼
第 2 步 渲染+反解: render 端点拿 token_ids → detokenize 反解
        ├─ render/detokenize 后 prompt 已不正确 ──▶ CHAT_TEMPLATE
        ▼ prompt 正确
第 3 步 同一组 token_ids 直发 /v1/completions(绕过全部 parser)
        ├─ 原始输出乱码/无意义重复/错误 token ──▶ MODEL_NUMERIC
        ├─ 有完整工具调用文本但无 tool_calls ──▶ TOOL_PARSER
        ├─ 有 think 标记但无 reasoning_content ──▶ THINKING_PARSER
        ├─ 原始输出完整正常但 chat 内容缺失/错位 ──▶ SERVICE_OUTPUT
        ├─ 只有 thinking、无最终回答/工具调用 ──▶ MODEL_INCOMPLETE
        ├─ 带 tools 但无任何工具调用格式 ──▶ MODEL_OUTPUT
        └─ 证据不完整/接口不支持 ──▶ INCONCLUSIVE
```

渲染降级链：render 端点 → `/tokenize`；`local`（本地 tokenizer）需 transformers，
本 Skill 不支持。降级后 `evidence_limitation` 如实标注口径差异，不声称已排除服务层。

## 定界标签与建议动作

| 结论 | 含义 | 建议动作 |
|---|---|---|
| `REQUEST` | 原始请求本身异常 | 按第 1 步明细修正请求 |
| `CHAT_TEMPLATE` | render/反解后的 prompt 已不正确 | 检查 chat template、`chat_template_kwargs`、tools 格式、启动参数，不进入逐层打点 |
| `MODEL_NUMERIC` | 原始输出已是乱码/重复/错误 token | 回到自检；再 logprobs/logits 标杆对比，再 msprobe 逐层定位 |
| `MODEL_INCOMPLETE` | 原始输出只有 thinking、无最终回答 | 增大 max_tokens、检查停止条件；仍不完整则逐层诊断 |
| `TOOL_PARSER` | 有完整工具调用文本但无 tool_calls | 检查 `--tool-call-parser` 与模板匹配 |
| `THINKING_PARSER` | 有 think 标记但无 reasoning_content | 检查 `--enable-reasoning`/`--reasoning-parser` |
| `SERVICE_OUTPUT` | 原始输出正常但 chat 内容缺失/错位 | 检查输出处理层，人工对照证据（启发式） |
| `MODEL_OUTPUT` | 带 tools 但无工具调用格式 | 排查 prompt 诱导/工具描述/模型能力/采样参数 |
| `INCONCLUSIVE` | 证据不完整 | 明确缺失证据，不猜测根因 |
| `PASS` | 链路正常，问题不在此链路 | 转 logprobs-diff 看数值分布 |

判定细则见 [references/decision-tree.md](references/decision-tree.md)。

## 关键约束（不可为了跑通而改变）

- 同一组 token IDs：第 3 步 completions 的 prompt 必须是第 2 步 render 返回的同一组
  token_ids，不是手写/重渲染文本。
- 请求原样使用：不为定位删除 `tools`、`chat_template_kwargs` 等字段简化请求。
- 双次生成条件一致：completions 与 chat 的模型名、采样参数（含 stop/top_p/seed）
  与停止条件逐项一致并留存证据（K6）。
- 不重发：服务不支持保留特殊 token 的扩展字段时，第 3 步失败不自动重发，如实记录
  并将结论标为 INCONCLUSIVE。
- 乱码优先于 parser：原始输出乱码时 MODEL_NUMERIC 优先于 TOOL_PARSER。
- 结论可叠加：verdict 为列表，primary 取优先级最高者。

## 目录结构

```
three-step-demarcation/
├── SKILL.md
├── references/
│   ├── contract.md               # 唯一权威契约
│   ├── step1-request-check.md
│   ├── step2-render-verify.md
│   ├── step3-raw-parse.md
│   ├── decision-tree.md
│   └── profiles.json             # 建议话术与乱码阈值
├── assets/request.example.json
└── scripts/
    ├── demarcation.py            # 公开入口
    └── demarcation/
        ├── cli.py contract.py inputs.py runner.py evidence.py
        ├── judge/ verdicts.py decide.py coverage.py
        ├── checks/ markers.py request.py render.py garbage.py
        └── probe/ http.py capability.py replay.py
```
