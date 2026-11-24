# decision-tree.md —— 三步法判定树

## 口径来源（正版原文）

> 界定思路：
>
> 1. 如果模型原始输出（指 `/v1/completions` 的响应）本身没有任何工具调用格式，
>    说明模型在当前 prompt 下没有生成 tool call。问题更可能在模型能力、prompt
>    诱导、工具描述或采样参数。
> 2. 如果模型原始输出里有完整工具调用文本，但 `/v1/chat/completions` 没有返回
>    `tool_calls`，问题更可能在 tool call parser。
> 3. 如果模型原始输出里有 `<think>...</think>` 或模型约定的 thinking 标记，但
>    chat 响应没有 `reasoning_content`，问题更可能在 thinking parser。
> 4. 如果模型原始输出只有 thinking，没有最终回答或工具调用，说明 parser 并没有
>    吞掉内容，模型原始生成就是不完整的。
> 5. 如果模型原始输出完整正常，但 chat 响应内容缺失、错位或格式被破坏，问题更
>    可能在服务输出处理层（parser / detokenize / stop 处理 / 响应封装）。
> 6. 如果模型原始输出已是乱码、无意义重复或错误 token（典型案例：DSML 分隔符 +
>    空白符的无限循环），问题在模型/推理链路本身，不是 Chat Parser 问题。
> 7. 如果 `/v1/chat/completions/render` 或 `/detokenize` 阶段得到的 prompt 已经
>    不正确，优先检查 chat template、`chat_template_kwargs`、tools 格式和 vLLM
>    启动参数。
>
> 第一步准备原始请求（prompt 是否正常）；第二步渲染加反解从而判断 prompt 是否
> 正确；第三步解析原始输出。
>
> 原始输出"没有 tool call"不能单独证明模型精度异常；模型可能因 prompt、工具
> 描述或能力原因没有选择调用工具。只有原始生成本身出现乱码、重复、错误 token，
> 或与标杆最终分布明显不同时，才继续模型精度定位。

## 判定树（实现口径，与 scripts/three_step_demarcate.py 同步维护）

```
第 1 步 请求静态校验（请求原样使用, 不简化）
  ├─ messages 空(致命) ──────────────▶ 终止, 退出码 3
  ├─ 任一 FAIL ─────────────────────▶ verdict += REQUEST (primary)
  │    (tools 结构错等非致命 FAIL: 继续后续步骤留证)
  └─ PASS/WARN ─────────────────────▶ 进入第 2 步

第 2 步 渲染+反解（render 端点 → tokenize → local, 逐级降级）
  ├─ 三种渲染途径均不可用 ──────────▶ 终止, 退出码 3
  ├─ 任一 FAIL(未填充变量/消息丢失/空) ──▶ verdict = [CHAT_TEMPLATE] (primary)
  │                                        第 3 步仍执行(坏 prompt 留证)
  └─ PASS/WARN ─────────────────────▶ 进入第 3 步
       降级提示: mode != render 时 meta.evidence_limitation 标注口径差异

第 3 步 原始输出 vs chat 解析结果（completions 直发同一组 token_ids）
  ├─ raw text 为空 ─────────────────▶ verdict = [INCONCLUSIVE]
  only_thinking = has_think && 无 toolcall 标记 && (未闭合 || 闭合后为空)
  ├─ only_thinking ──────────────────▶ verdict = [MODEL_INCOMPLETE]
  ├─ garbage.is_garbage ─────────────▶ verdict += MODEL_NUMERIC
  │    (乱码/无意义重复优先于 parser: 分隔符死循环可能误命中工具标记正则)
  ├─ has_toolcall_text && chat.tool_calls 空 ──▶ verdict += TOOL_PARSER
  ├─ has_think(已闭合) && chat.reasoning 空 ───▶ verdict += THINKING_PARSER
  │    (TOOL_PARSER 与 THINKING_PARSER 可叠加)
  ├─ !has_toolcall_text && !garbage && raw 回答>=10可见字符
  │   && chat.content 空 && chat.tool_calls 空 ─▶ verdict += SERVICE_OUTPUT
  │    (服务输出处理层, 启发式, 需人工复核)
  ├─ 请求带 tools && !has_toolcall_text ────────▶ verdict += MODEL_OUTPUT
  │    (模型在当前 prompt 下没有生成 tool call, 不能单独证明精度异常)
  └─ raw 与 chat 双双正常 ────────────▶ verdict = [PASS]
       (服务化解析链路正常 → 转 logprobs-diff 看数值分布)
```

## verdict 优先级

`REQUEST > CHAT_TEMPLATE > MODEL_NUMERIC > MODEL_INCOMPLETE > TOOL_PARSER ≈ THINKING_PARSER > SERVICE_OUTPUT > MODEL_OUTPUT > INCONCLUSIVE > PASS`

- 前两步的问题是"更上游"的问题（prompt 都不对，输出没有意义）；
- `MODEL_NUMERIC` 高于 parser 类：乱码/无意义重复是模型层直接证据
  （典型案例：DSML 分隔符+空白符的无限循环），此时 parser 结论无意义；
- `MODEL_INCOMPLETE` 高于 parser 类：口径明确"parser 并没有吞掉内容，
  模型原始生成就是不完整的"，根因在生成侧；
- `SERVICE_OUTPUT` / `MODEL_OUTPUT` 依赖启发式或"不能单独证明精度异常"的口径，
  排在显式证据之后，报告需注明人工复核项；
- `verdict` 为列表，`primary` 为优先级最高者；报告需列出全部叠加结论。

## 各结论的证据要求（写报告时必引用）

| 结论 | 必须引用的证据 |
|---|---|
| REQUEST | 第 1 步明细中具体 FAIL 项 |
| CHAT_TEMPLATE | 第 2 步具体 FAIL 检查项 + prompt_head（坏在哪） |
| MODEL_NUMERIC | garbage 检测命中的具体特征（多样性/重复率/alnum 占比）+ raw text 片段 |
| TOOL_PARSER | raw 中命中的 toolcall 模式 + chat.tool_calls 数量（通常为 0） |
| THINKING_PARSER | raw 中 think 标记（开/闭）+ chat.reasoning 为空 |
| SERVICE_OUTPUT | raw 回答内容（含长度）+ chat.content 为空 + 人工复核记录 |
| MODEL_INCOMPLETE | only_thinking 判定依据（未闭合 / 闭合后空）+ finish_reason |
| MODEL_OUTPUT | 请求含 tools + raw text 全文无任何工具调用格式（说明检查过全文） |
| INCONCLUSIVE | 缺失的具体证据项（raw 为空 / 接口不支持 / 降级口径受限） |
