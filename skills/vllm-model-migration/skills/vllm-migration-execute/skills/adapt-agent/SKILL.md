---
name: adapt-agent
description: 根据预检、固定上游参考及明确允许范围，为 vLLM/vLLM-Ascend 适配模型实现、forward、权重映射、config/processor 和必要的平台兼容逻辑。用于 adaptation_required 路线；采集源码上下文、核验实际差异和定向证据，生成适配交接。
---

# 模型实现适配

将原始模型语义适配到明确的 vLLM/vLLM-Ascend 基线。模型结构由模型实现表达，平台兼容按实际能力选择；保留原始 architecture、config 和权重。实现应可审查、可回退，并明确尚未覆盖的能力。

本 Skill 拥有源码适配及交接。`vllm-model-precheck` 决定路线，`vllm-model-register` 拥有源码/plugin 注册，`verify-agent` 唯一拥有运行载体、服务生命周期和真实功能验证。本脚本不执行模型、不自动转换模型、不注册、不构建或启停服务、不提交或发布。

## 执行步骤

1. 读取已完成预检及其固定输入。只在 `adaptation_required` 路线进入实现，绑定 selected_architecture 及其支持矩阵；版本证据不足、阻塞或其他路线按原路线交接。明确任务、模态、dtype、量化、目标版本、参考实现和允许改动范围。仅列本次要求的功能，不默认增加 graph、MTP、多机或精度/性能门槛。
2. 准备彼此隔离的干净基线与可编辑工作副本。固定完整来源树身份、revision、获取依据和参考资料摘要；`origin` 是来源获取记录，不把填写的 revision 当成 Git/官方内容已被自动证明。读取[契约](reference/contract.md)，运行 `capture`。资料或源码留在包外；计划模式仅验证形状。
3. 分析嵌套 config、forward、attention/KV、并行层、权重和 processor。按[实现检查单](reference/workflow-checklist.md)记录 Change Shape Map、失败边界、权重映射、依赖与来源；按[平台兼容](reference/operator-compatibility.md)检查目标能力。源码扫描或 Torch/Triton 名称不证明 NPU 支持。
4. 在工作副本中实施最小改动。参考代码须审查许可、版本 API、模型数学及平台约束；不得整体升级环境、改 architecture 掩盖不兼容、自动切换模型或研发新算子。模型结构与通用平台能力分别归属，平台逻辑不根据某个模型名字分支。若必须触及原有硬热点，按现行规范拆分职责并保持公共 API；审计全部实际差异及受影响直接同级目录。
5. 对当前完整源码执行定向测试：forward/reference 数学、完整 key/shape/dtype、真实 loader 的丢失/重复/未知权重及 TP/融合行为、config/processor 和受影响平台路径。测试由执行步骤实际运行；保存实际 argv、退出结果、原始日志和摘要。生成绑定本次源码 fingerprint 与原始 config 的检查记录和显式权重映射；不能只填写 pass。映射生成方式必须从配置及 loader 语义推导，不能根据 checkpoint 本身反推一个总能通过的期望表。
6. 运行 `check`；阅读所有 diff、排除理由和检查证据。静态脚本核对完整来源树、允许范围、Python 编译、safetensors 元数据、映射区间及外部记录身份。只有全部必需检查通过才生成 completed/pass 适配交接；外部检查状态或证据缺失时保留 blocked/failed。脚本不证明测试日志的语义真实性，执行者必须核对原始输出及范围。
7. 使用完整资源副本中的注册公开 CLI，分别按需要验证源码或 out-of-tree plugin。注册变更独立记录，不混入适配 diff；registry 的一般结构兼容改动可以在明确范围内审查，但不得在本步骤添加 architecture→class 映射。
8. 将已核对的适配文件及注册变更交接给 `verify-agent`。要求运行输入固定实际载体内全部适配文件、注册文件、模型资产和版本；可复用逐文件匹配的已有载体，但必须重新确认身份、worker、真实权重与功能。传入适配共同回执；载体内再次核验注册，然后真实权重、完整文本/chat/SSE 和请求模态验收。语义待复核不能成功。任何服务启停、构建和资源清理由验证入口执行。
9. 交付源码差异/摘要、来源与配置身份、权重映射、测试证据、注册及部署回执、真实验收/离线测试/未验证项。保留失败和部分结果；不把适配静态 completed 表述成整模迁移完成。成功服务按明确策略交接，复用服务不自动停止。

## 独立 CLI

需要 Python 3.10+，脚本本身仅依赖标准库；完整资源可以复制到授权目标环境，不依赖维护仓库后端或 SSH 调度。

```bash
python3 /完整资源/skills/vllm-model-migration/skills/vllm-migration-execute/skills/adapt-agent/scripts/adapt.py \
  --request /包外/capture.request.json --work-root /包外/runs \
  --run-id migration-001 --action capture --plan

python3 /完整资源/skills/vllm-model-migration/skills/vllm-migration-execute/skills/adapt-agent/scripts/adapt.py \
  --request /包外/capture.request.json --work-root /包外/runs \
  --run-id migration-001 --action capture

python3 /完整资源/skills/vllm-model-migration/skills/vllm-migration-execute/skills/adapt-agent/scripts/adapt.py \
  --request /包外/check.request.json --work-root /包外/runs \
  --run-id migration-001 --action check
```

每次生成独立 attempt，不覆盖旧回执。目录读取失败不得静默跳过；资料 JSON 深度限制及输出脱敏规则见契约。`capture` 冻结上下文；`check` 消费其 SHA256 绑定回执。`--plan` 不修改源码、不读取目标资产、不构建、启动或写运行目录。参数和 JSON 见[契约](reference/contract.md)。

按需读取：[实现检查单](reference/workflow-checklist.md)、[平台兼容](reference/operator-compatibility.md)、[量化边界](reference/quantization-guide.md)、[失败定位](reference/troubleshooting.md)、[支持度来源](reference/supported-models.md)。
