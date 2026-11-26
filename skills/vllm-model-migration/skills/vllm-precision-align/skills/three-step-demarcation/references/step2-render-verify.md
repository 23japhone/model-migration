# step2-render-verify.md —— 第 2 步：渲染 + 反解，验证 prompt 是否正确

> 目标：把 chat 请求（messages + tools + chat_template_kwargs）还原成**服务实际
> 送入模型的 prompt**，验证它正确。prompt 若已不正确，问题位于模型计算之前，
> 后面一切解析都无从谈起，也**不进入** msprobe 逐层打点。

## 渲染途径

单题请求的 JSON 字段 `render_mode` 默认取 `auto`。脚本先用服务端 `render`，无法取得可反解的 Token ID 和非空文本时再用服务端 `tokenize`。显式指定 `render` 或 `tokenize` 时仅执行该途径。实际途径、回退经过和证据限制分别记录在 `detailed_report.meta` 的 `render_mode`、`render_fallback_chain` 和 `evidence_limitation` 中。

| 途径 | 怎样取得 Token ID | 怎样核对 prompt |
| --- | --- | --- |
| `render` | 向 `/v1/chat/completions/render` 提交原始 Chat 请求，其中缺少模型名时补入本次服务模型。 | 把服务返回的 Token ID 交给 detokenize 反解。第三步的 Completion 使用这组 ID。 |
| `tokenize` | 向服务端 tokenize 接口提交消息，并按接口版本传递支持的工具和模板参数。 | 同样用服务端 detokenize 反解。该途径对工具和模板参数的覆盖受版本限制，报告会保留这一限制。 |

已识别版本使用 `/tokenize` 与 `/detokenize`。版本未知时脚本探测路径前缀，实际使用的前缀见 `detailed_report.meta.tokenize_prefix`。当所选途径全部不可用时，单题报告记录 `status=failed` 和 `diagnostic.type=demarcation_unavailable`，退出码为 1。[接口与退出码](contract.md)

## 渲染结果检查点

| 检查点 | 级别规则 | 说明 |
|---|---|---|
| `prompt 非空` | 空 → FAIL（致命） | |
| `无未填充模板变量` | 文本残留 `{{` 或 `{%` → FAIL | 模板变量没被消费，说明 kwargs/模板不匹配 |
| `各条消息内容已进入 prompt` | 任一非空文本消息 content（归一化空白后取前 24 个字符）在 prompt 中找不到 → FAIL | 消息丢失/被截断 |
| `工具描述进入 prompt` | tools 的 function.name 在 prompt 中找不到 → **WARN**（不判 FAIL） | 核对模板是否渲染工具，以及本次 tokenize 是否能传递工具字段 |
| 人工复核项（不自动判级） | role 顺序与边界；BOS/EOS、generation prompt、thinking/tool-call 特殊标记是否正确、有无重复添加 | 通用脚本难以覆盖所有模板体系，对照 rendered_prompt.json 人工看 |

## 判定

- 任一检查为 `FAIL` 时，`step2_render.ok` 为 `false`。单题定界继续做 Completion 和 Chat 重放并保留证据，最终分类还取决于请求校验和第三步的结果。
- 全部检查为 `PASS` 或 `WARN` 时，`step2_render.ok` 为 `true`，继续核对生成结果。

## CHAT_TEMPLATE 结论下的排查清单

1. `--chat-template` 启动参数是否覆盖了模型自带模板（版本是否配套）；
2. `chat_template_kwargs` 的键是否为该模板支持（如 Qwen3 的
   `enable_thinking`）；
3. tools 的 JSON Schema 是否符合模板预期（有的模板要求 `description` 非空）；
4. 核对服务端框架版本及这次实际使用的模板，对照 `rendered_prompt.json` 中的反解结果。
