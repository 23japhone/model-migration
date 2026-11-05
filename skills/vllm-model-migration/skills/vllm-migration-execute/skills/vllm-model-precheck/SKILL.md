---
name: vllm-model-precheck
description: 适用于在华为昇腾上迁移模型前核对固定 vLLM/vLLM-Ascend 版本支持度，或需要根据原始模型配置判断是否可进入部署、是否缺少注册、是否需要实现适配的场景。
---
# vLLM-Ascend 模型支持度预检

从原始模型资料、目标源码和已阅读的官方资料形成静态支持矩阵。任务独立运行；只读取资料和保存包外证据，不导入目标 Python，不加载权重、不改模型配置或注册表，不启动 Docker、SSH、NPU 或服务。

## 工作流程

1. 阅读[请求与结果契约](references/contract.md)，明确模型路径、任务/模态、dtype/量化、所需功能、目标硬件、平台和精确版本。使用已有准备交接时，重新核对其终态、模型回执和当前 config 身份；metadata 可以用于预检，不能用于真实部署验收。
2. 取得对应目标版本的源码资料。先按[资料核对方法](references/sources.md)阅读 version_pair 和 platform_support 资料，保存原文、原文 SHA256、来源、日期、覆盖范围和复核说明。资料未读、目标不一致或支持情况未知时保留 review_required，不能仅填 pass。
3. 使用[请求样例](assets/request.example.json)生成本次请求。相对模型路径基于 work-root，源码和资料路径基于请求文件；跨机器时在实际资产环境执行完整资源副本，不把本机路径传给另一台主机。
4. 从实际资源路径运行公开 CLI 的 --plan，核对参数和保护边界。它不读取模型/源码内容、不验证资料语义、不写目录，不能把计划退出 0 当作预检通过。
5. 实际运行预检。脚本保留根 architectures 和嵌套配置，使用 AST 核对内置常量注册映射及 class 声明，检查 chat_template、必要 processor 和量化声明，并逐条核对资料目标和摘要。动态注册、re-export、未知配置或资料不足如实阻塞，不猜测映射、不执行 remote code。
6. 阅读原始快照、候选矩阵和原因。supported_candidate 仅可进入部署验证；registration_required 要先注册；adaptation_required 要先完成允许范围内的实现适配；blocked 保留缺口。模型名称、非空 class、资料记录 pass 或旧报告均不能证明实际推理。

```bash
python3 "/实际技能目录/scripts/precheck.py" \
  --request "/实际请求目录/precheck.json" \
  --work-root "/用户指定证据根" --run-id "precheck-001" --plan
# 核对后去掉 --plan，执行只读资料预检。
```

## 路线与交接

| route | 含义 |
|---|---|
| supported_candidate | 已定位目标内置实现且必需静态资料通过；仍须在实际运行载体复验注册并验证真实权重 |
| registration_required | 本次架构已有明确实现、平台资料通过，但内置映射缺失；不证明已有 plugin 的运行注册状态 |
| adaptation_required | 有固定摘要的具体参考实现和允许改动范围，已识别实现或平台缺口；不承诺改动一定能成功 |
| blocked | 配置、源码解析、版本、平台、资料或允许范围不足，不能继续依赖本结果部署 |

交付 precheck-manifest.json 和快照的绝对路径，沿用[共同结果 Schema](../../../../references/task-result.schema.json)的 1.0 外层。状态为 completed 表示静态分析已完成；support=pass 仅用于 supported_candidate，runtime_support 始终 not_evaluated。报告必须说明引用的源码 revision 是声明还是实际核验，不把资料检查当作目标安装版本证明。

证据写入失败、取消或清单未完整保存时不能宣告完成；保留原尝试和独立分支资料，不自动重试、升级、修复、提交或发布。本 Skill 结束于支持度交接，由调用方在已授权范围内选择后续任务。
