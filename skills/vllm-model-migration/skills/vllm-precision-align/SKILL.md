---
name: vllm-precision-align
description: 根据已有部署和数据交接编排 vLLM/vLLM-Ascend 精度测试，按需委托数据准备或部署任务，或汇总已有 EvalScope/AISBench 产物的只读分析时使用。
---

# 精度测试阶段

先核验本次范围的服务、数据及评测输入，再通过公开CLI调用 `verify-agent-plus`，汇总实际测量、有效性、可比性、目标和资源交接。阶段不重新实现评分、部署或下载，不进入诊断、调参及自动重测。

## 工作步骤

1. 阅读[阶段契约](references/contract.md)，将每个独立精度请求固定为包外JSON及SHA256。模型、端点、实际served model name、数据、split和样本、评测工具环境/配置、基线及目标沿用精度任务的显式输入，不绑定默认模型或数据集。
2. 优先复用完整已有交接。缺少数据交接时，声明 `datasets` 请求，由 `vllm-dataset-prepare` 唯一负责准备；缺少部署交接时，声明 `deployments` 请求，由 `verify-agent` 唯一负责部署及功能核验。部署必须显式keep，语义待复核时先交付原始证据，不能当成通过。需要源码适配或注册时先取得对应迁移交接，不在精度阶段增加一套实现。
3. 用 `--plan` 检查顶层请求与包外输出边界；计划不读取引用、不执行Python评测配置、不安装依赖、不调用服务或子任务。实际运行再检查来源摘要、资产输出边界和子任务计划。共享依赖只调用一次，不覆盖既有交接或改写原请求文件。
4. 新请求默认 EvalScope，省略 evaluator.version 或使用 latest 时由精度任务核验官方最新稳定版；旧 AISBench Python 配置及显式版本请求继续兼容。阶段不补写 backend/version，不安装依赖、不回退工具；版本或配置不满足时明确阻塞该项。
5. `run` 显式委托必需准备/部署，随后调用每个精度请求；`analyze` 只调用精度任务的历史分析入口，禁止准备、部署及新推理。完整原始配置、预测和评分仍由精度任务保存，阶段引用固定回执，不以分数或退出0代替有效评测。
6. 独立项失败继续其他就绪项，取消保留已完成结果并只通知本次自有CLI收敛；服务生命周期始终归 `verify-agent`，借用服务保持运行，不按名称清理。证据不能保存时停止后续外部效果。
7. 交付阶段manifest、终态task-ledger、绑定台账摘要的handoff、逐项精度报告、原样实际评测条件和服务资源索引。有效评测目标fail仍是completed；缺GPU/目标不宣布对齐或达标，不自动按分数重测。分别报告真实验收、离线测试、未验证项。

## 公开CLI

保留完整总 Skill 的嵌套资源，通过总 Skill 布局索引定位固定公开 CLI。Python3.10+，阶段自身只依赖标准库，不导入兄弟Skill私有实现或维护后端。

```bash
python3 /完整资源/skills/vllm-model-migration/skills/vllm-precision-align/scripts/precision.py \
  --request /包外/stage.json --work-root /包外/runs --run-id precision-stage-001 --plan

python3 /完整资源/skills/vllm-model-migration/skills/vllm-precision-align/scripts/precision.py \
  --request /包外/stage.json --work-root /包外/runs --run-id precision-stage-001

python3 /完整资源/skills/vllm-model-migration/skills/vllm-precision-align/scripts/precision.py \
  --action analyze --request /包外/analysis-stage.json --work-root /包外/runs --run-id analysis-stage-001
```

示例：[默认 EvalScope 复用已有交接](assets/run.example.json)、[AISBench 兼容请求](assets/run.aisbench.example.json)、[缺数据或部署交接](assets/dependencies.example.json)、[只读分析](assets/analyze.example.json)。示例路径和摘要必须替换，不能作为运行证据。

评测能力与实际运行环境边界以 `verify-agent-plus` 为准；当前支持 EvalScope Native/openai_api 与 AISBench，采用独立评测环境、同执行主机 HTTP、非流式和单次生成；本机其他 IP 在运行时验证本地归属。具体配置、样本与原生证据见[精度任务契约](skills/verify-agent-plus/references/contract.md)。analyze 按原工具和版本只读核验，不联网重选版本、不重新评分。完整资源副本运行不等于宿主发现或真实模型验收。

客户流程可为 evaluation 提供 `blocked_reason` 保存未就绪项，并让其他独立项继续；也可提供 `result` 引用复用同一完整输入的有效原 run。复用前委托 verify-agent-plus 的公开 analyze 核验，再保留原 run 引用，不生成新测量。结果/来源变化阻塞该项；契约见 references/contract.md。
