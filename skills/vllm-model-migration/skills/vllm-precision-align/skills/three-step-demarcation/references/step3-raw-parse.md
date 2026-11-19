# step3-raw-parse.md —— 第 3 步：解析原始输出

> 目标：拿**绕过一切解析器（tool call parser / thinking parser / detokenize
> 过滤）的模型原始输出**，与 chat 接口的解析结果对比，按 decision-tree.md
> 的规则定界。

## 双请求

1. **原始输出**：`POST /v1/completions`，
   `prompt = 第 2 步 render 返回的同一组 token_ids`（整数数组直发，
   `meta.completions_prompt_form = "token_ids"`），采样参数与 chat 请求一致；
   - 请求带扩展字段（正版要求，尽量保留特殊 token 与停止位置）：

     | 字段 | 值 | 目的 |
     |---|---|---|
     | `skip_special_tokens` | `false` | 不过滤 `<think>` / 工具标记等特殊 token |
     | `include_stop_str_in_output` | `true` | 保留停止字符串，观察真实停止位置 |
     | `spaces_between_special_tokens` | `false` | 特殊 token 不插空格，保匹配 |
     | `return_token_ids` | `true` | 返回生成 token ids，便于精确对位 |

   - 扩展字段不被支持（HTTP 400）→ 去掉扩展字段降级重试，并在
     `meta.extension_fields_dropped = true` 记录；此时特殊 token 可能被过滤，
     think/tool 标记检测置信度受限，报告须注明；
   - 响应 `choices[0].text` 即模型原始输出 raw text；该路径不经过任何
     parser，所见即模型所生成。
2. **解析结果**：`POST /v1/chat/completions`，**原始请求全部字段透传**
   （messages / tools / tool_choice / `chat_template_kwargs`），仅覆盖模型名
   与采样参数——否则 chat 路径渲染口径与 badcase 不一致，对比无效；
   - `choices[0].message.tool_calls`（工具调用解析结果）
   - `choices[0].message.reasoning_content`（vLLM 系）/ `reasoning`（SGLang，
     兼容两者）
   - `choices[0].message.content`

## 原始输出特征检测

### tool call 文本标记（内置正则集合）

| 模式 | 覆盖模板 |
|---|---|
| `<tool_call>` | Qwen3 / Hermes 风格 |
| `<\|tool_calls_section_begin\|>`、`<\|tool_call_begin\|>` | Qwen3 部分版本 |
| `<function=` | Llama function-call 风格 |
| `<\|python_tag\|>` | Llama 3.1 |
| `\[\s*{"name"\s*:` | JSON 数组裸调用 |
| `"function"\s*:\s*{` | JSON 对象调用 |

- 命中任一 → `has_toolcall_text = true`（记录命中的模式）；
- **检测不到 ≠ 没有工具调用**：模型可能用非标准格式表达调用，需人工看
  raw text（json 中保留全文）；
- 扩展方式：改脚本 `TOOL_PATTERNS` 常量（团队内部模型模板可自行追加）。

### thinking 文本标记

| 模式 | 覆盖 |
|---|---|
| `<think>` / `</think>` | Qwen3 / DeepSeek-R1 系 |
| `<reasoning>` / `</reasoning>` | 部分开源约定 |

派生特征：

- `think_closed`：检测到闭合标记（`</think>` 或 `</reasoning>`）；
- `content_after_think`：闭合标记之后的剩余文本（strip 后）；
- `only_thinking`：`has_think_text` 且无任何 toolcall 标记，且
  （未闭合 或 `content_after_think` 为空）——即"只有 thinking，
  没有最终回答或工具调用"。

### 乱码 / 无意义重复检测（`garbage`，对应正版"乱码、无意义重复或错误 token"）

| 特征 | 阈值 | 说明 |
|---|---|---|
| `alnum_ratio` | < 0.05 | 可见字符几乎全为符号/标点（如 `#`、`,`、分隔符循环） |
| `char_diversity` | < 0.12（100 字符滑窗最小值） | 字符多样性过低（正常文本 ~0.3+） |
| `top_token_freq` | > 0.6 | 最高频 token 占比过高（同一 token 死循环） |
| `dup_ngram_ratio` | > 0.7（仅 ≥200 可见字符时启用） | 8-gram 重复率（整句/整段循环；短文本与合法 JSON 工具数组不启用，防误判） |
| 空白特判 | 可见字符为 0 且文本非空 | 原始输出几乎全为空白/不可见字符 |

- 命中任一 → `garbage.is_garbage = true`（记录命中的特征与数值）；
- 可见字符 < 40 时不判定（`checked = false`）；
- **乱码优先于 parser 类判定**：DSML 分隔符死循环可能误命中工具标记正则，
  此时结论应为 `MODEL_NUMERIC` 而非 `TOOL_PARSER`（典型案例即正版文档中
  DeepSeek V4 Pro 部署后 `#`/`,`/DSML 分隔符无限重复）。

## 对比判定（详见 decision-tree.md）

| raw 特征 | chat 特征 | 结论 |
|---|---|---|
| 乱码/无意义重复/错误 token | — | `MODEL_NUMERIC`（模型/推理链路问题，不是 Chat Parser；回自检，再 logprobs/logits 标杆对比） |
| 有完整工具调用文本 | `tool_calls` 为空 | `TOOL_PARSER` |
| 有 think 标记（已闭合） | reasoning 为空 | `THINKING_PARSER` |
| `only_thinking` | — | `MODEL_INCOMPLETE`（parser 并没有吞掉内容，模型原始生成就不完整） |
| 正常自然语言回答（非乱码、非纯工具调用，≥10 可见字符） | content 整体缺失且无 tool_calls | `SERVICE_OUTPUT`（服务输出处理层，启发式，需人工复核） |
| 请求带 tools、无任何工具调用格式 | — | `MODEL_OUTPUT`（模型未选择工具，**不能单独证明精度异常**；查 prompt 诱导 / 工具描述 / 模型能力 / 采样参数） |
| raw text 为空 | — | `INCONCLUSIVE`（证据不足，不猜测根因） |
| 有工具调用文本且 chat 有 tool_calls；有 think 且 chat 有 reasoning | — | `PASS`（服务化解析链路正常，问题在别处，转 logprobs-diff 看数值分布） |

## 证据留存（正版要求）

脚本自动落盘（`--evidence-dir`，默认与输出 json 同目录）：

- `raw_completion.json`：completions 完整响应（含 raw text 全文）；
- `chat_response.json`：chat 对照完整响应（含 message 全量字段）；
- 加上第 2 步的 `rendered.json` / `rendered_prompt.json` 与输入
  `badcase.json`，共 5 份——人工复核与升级 PAE 直接可用；
- `finish_reason` 一并记录（`length` 常与 MODEL_INCOMPLETE 相关：max_tokens
  耗尽导致"只有 thinking"）。
