# step2-render-verify.md —— 第 2 步：渲染 + 反解，验证 prompt 是否正确

> 目标：把 chat 请求（messages + tools + chat_template_kwargs）还原成**服务实际
> 送入模型的 prompt**，验证它正确。prompt 若已不正确，问题位于模型计算之前，
> 后面一切解析都无从谈起，也**不进入** msprobe 逐层打点。

## 渲染途径（三种模式，逐级降级；`--render-mode auto` 自动选择）

### 模式 A：服务端 render 端点（`render`，正版首选，无口径损失）

```
POST /v1/chat/completions/render   ← 用完整原始请求(badcase.json 原样)
    → token_ids（服务实际送入模型的整数序列）
POST /v1/detokenize  {"model": M, "tokens": [...]}
    → prompt 文本
```

- 请求体就是 badcase.json 原文（messages / tools / tool_choice /
  `chat_template_kwargs` 全部保留），不重构、不简化；
- 拿到的 `token_ids` 是**服务自己的渲染结果**，第 3 步直发这同一组
  token_ids，保证与 chat 路径同口径——这是三步法的核心不变量；
- 兼容多种响应字段名（`token_ids` / `tokens` / `input_ids`）；
- render 失败（404/字段错）时优先检查请求格式、chat template、模板参数和
  服务启动配置，然后降级模式 B。

### 模式 B：服务端 tokenize + detokenize（`tokenize`）

```
POST /v1/tokenize    {"model": M, "messages": [...], "tools": [...],
                      "chat_template_kwargs": {...]}   → token ids
POST /v1/detokenize  {"model": M, "tokens": [...]}     → prompt 文本
```

- 局限：**部分版本 /tokenize 不透传 tools / chat_template_kwargs**（此时工具
  检查项降 WARN 并标注）；token_ids 与 chat 路径的口径可能有差异，
  `meta.evidence_limitation` 如实记录。

### 模式 C：本地渲染（`local`，需 `--tokenizer`，最后降级手段）

```python
tokenizer.apply_chat_template(
    messages, tools=tools, tokenize=False,
    add_generation_prompt=True, **chat_template_kwargs)
ids = tokenizer(text, add_special_tokens=False)["input_ids"]  # 重新编码出 token_ids
```

- 重新编码出 token_ids，第 3 步仍直发 token_ids（维持不变量）；
- 依赖 `transformers`（4.42+ 才支持 `tools=`；老版本自动降级为不带 tools
  渲染，工具检查项降 WARN）；
- tokenizer 目录用**与服务相同的模型目录**（保证模板一致）；
- 局限：本地模板/词表版本可能与服务端不一致，token_ids 未经服务端 render
  确认——`meta.evidence_limitation` 如实记录，结论置信度受限。

> 三种模式都不可用 → 运行错误（退出码 3）。无法保证 token_ids 相同时，
> 不得声称"已排除服务层"。

## 渲染结果检查点

| 检查点 | 级别规则 | 说明 |
|---|---|---|
| `prompt 非空` | 空 → FAIL（致命） | |
| `无未填充模板变量` | 文本残留 `{{` 或 `{%` → FAIL | 模板变量没被消费，说明 kwargs/模板不匹配 |
| `各条消息内容已进入 prompt` | 任一 system/user 消息 content（取前 24 字符片段）在 prompt 中找不到 → FAIL | 消息丢失/被截断 |
| `工具描述进入 prompt` | tools 的 function.name 在 prompt 中找不到 → **WARN**（不判 FAIL） | 模板可能本来就不渲染 tools（靠 parser 侧注入）、模式 B 未透传、或模式 C 老 transformers 降级；按模式给不同的提示文案，需人工确认 |
| 人工复核项（不自动判级） | role 顺序与边界；BOS/EOS、generation prompt、thinking/tool-call 特殊标记是否正确、有无重复添加 | 通用脚本难以覆盖所有模板体系，对照 rendered_prompt.json 人工看 |

## 判定

- 任一 **FAIL** → `render.ok = false` → verdict = `CHAT_TEMPLATE`，
  第 3 步仍执行（用坏 prompt 跑一次留证），但 primary 结论为 CHAT_TEMPLATE；
- 全 PASS/WARN → `render.ok = true`，进入第 3 步。

## CHAT_TEMPLATE 结论下的排查清单

1. `--chat-template` 启动参数是否覆盖了模型自带模板（版本是否配套）；
2. `chat_template_kwargs` 的键是否为该模板支持（如 Qwen3 的
   `enable_thinking`）；
3. tools 的 JSON Schema 是否符合模板预期（有的模板要求 `description` 非空）；
4. vLLM / vLLM-Ascend / SGLang 版本升级后模板行为变化；
5. 用模式 A（render 端点）与模式 C（本地渲染）**互相对照**，快速区分
   "模板本身错" vs "服务没用到对的模板"。
