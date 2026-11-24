# step1-request-check.md —— 第 1 步：准备原始请求（静态校验）

> 目标：确认送进服务的**原始请求本身是正常的**，排除"请求构造错误"这一最低成本根因。
> 纯本地 json 检查，不访问服务。
>
> **保护原始请求（正版口径）**：不要删除 `tools`、`chat_template_kwargs` 或特殊
> 字段来"简化"请求；若为固定随机性将采样改为 `temperature=0`，先保存原请求和
> 响应，并确认修改后仍能复现同一问题，否则继续用原始 badcase 并在结论中说明
> 随机性限制。

## 校验项清单

| 检查项 | 级别规则 | 说明 |
|---|---|---|
| `messages 存在且非空` | 缺失/空列表 → FAIL（致命） | 后续步骤无法进行 |
| `messages 结构` | 每条须为 dict，`role` ∈ {system, user, assistant, tool} 且 `content` 非空；否则 FAIL | content 目前只支持 str（多模态 content parts 记 WARN） |
| `tools 非空列表`（若提供） | 非列表/空列表 → FAIL | |
| `tools JSON 结构` | 每项 `type=="function"`，`function.name` 非空且不重复，`parameters`（若存在）为 dict；否则 FAIL | 常见错误：name 重复、parameters 写成 JSON 字符串 |
| `tool_choice 取值`（若提供） | 合法值：`auto` / `none` / `required` / `{"type":"function",...}`；其他 → WARN | |
| `采样参数合理性` | `temperature > 1` 或 `top_p > 1` → WARN | 高温采样会放大输出不稳定性，定界场景建议 0 |

## 处置规则

- 任一 **FAIL**：verdict 加入 `REQUEST`，逐条给修正建议；
  - `messages` 致命（空）→ 直接终止（退出码 3）；
  - 其余 FAIL（如 tools 结构错）→ **继续跑第 2、3 步**（构造性问题常同时引发
    渲染异常，多一份证据），但最终 verdict 以 `REQUEST` 为 primary。
- 仅 WARN：记录，继续。

## 常见请求构造错误（经验库）

1. tools 的 `parameters` 传成了 JSON **字符串**而非对象；
2. 同名工具重复注册（部分框架静默取后者）；
3. `tool_choice` 指定了不存在的函数名；
4. messages 里 assistant/tool 轮次的 content 为空字符串（模板渲染异常）；
5. `chat_template_kwargs` 里塞了模板不认识的键（部分模板直接报 Jinja 错）。
