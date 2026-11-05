---
name: vllm-migration-execute
description: 根据已完成的准备交接和目标运行要求，编排 vLLM-Ascend 模型预检、必要的实现适配与注册、真实权重功能验证，汇总阶段台账和部署交接。用于准备之后的模型迁移阶段。
---

# 模型迁移阶段

按目标版本、原始模型和请求能力完成迁移闭环。预检候选仅为静态路线；必需功能在指定运行载体和 NPU 上实际通过，证据完整后才能完成。本阶段不执行正式数据集精度评分、性能验收、多机调度或自动异常修复。

## 工作步骤

1. 读取准备清单，确认模型 full、镜像、目标版本和资料已完成；重新核对设备占用、完整权重、实际镜像及版本内容身份。使用[契约](references/contract.md)生成包外请求；路径属于当前执行环境，凭据使用已有授权配置，不进入请求、argv 或报告。
2. 通过公开阶段 CLI 运行 precheck；按 selected_architecture 的路线继续：
   - supported_candidate：跳过适配与开发注册，进入 verify；载体内注册仍需复验。
   - registration_required：调用 register 建立或复用映射，再进入 verify。
   - adaptation_required：先用 adapt 的公开步骤完成源码分析、capture、最小改动、定向验证及 check；将其固定回执和来源→运行根映射交给阶段，再调用 register/verify。CLI 不生成模型代码。
   - blocked：保留原因和资料，阻塞真实依赖，不自动切模型、改架构名、升级环境或循环修复。
3. 注册可以在同主机目标解释器中执行；指定 register_container 时，核对已有载体的完整 CID/image/StartedAt、复制完整注册资源并调用同一公开 CLI。此操作不创建或启停服务。开发与载体路径允许不同，architecture/module/class/文件内容必须一致；不能只凭开发 import 成功跳过最终载体检查。
4. 在 verify 请求中显式纳入全部适配文件和独立注册文件、原始资产、实际载体与资源策略。阶段注入本次准备/预检/适配/注册回执，通过 verify 公开入口执行。verify 唯一拥有构建、服务启停、真实权重和资源交接；复用服务不得自动停止，资源不足不清理其他任务。
5. 若语义待复核，阶段保持 blocked；阅读原始问题、完整文本/SSE、固定图片和响应摘要，通过 verify 的 review 生成独立回执。使用阶段 finalize 再次只读核验该 raw verify 的原始证据并生成新阶段结果，不重新部署。保留原报告，不把 HTTP 200、非空输出或 review_required 当成功。
6. 交付 migration-manifest、task-ledger、migration-handoff、全部子任务回执、源码/镜像/模型/设备/worker/请求证据，以及真实验收、离线测试、未验证项。检查实际服务最终状态；阶段 completed 不等于正式精度或性能验收。

## 调用

保留完整总 Skill 的嵌套资源，脚本通过总 Skill 布局索引定位任务公开 CLI，不导入任务私有实现、不依赖维护后端、不增加 SSH 框架。Python 3.10+ 标准库；目标运行依赖由对应任务明确检查。

```bash
python3 /完整包/skills/vllm-model-migration/skills/vllm-migration-execute/scripts/execute.py \
  --request /包外/stage.request.json --work-root /包外/runs --run-id migration-001 --plan

python3 /完整包/skills/vllm-model-migration/skills/vllm-migration-execute/scripts/execute.py \
  --request /包外/stage.request.json --work-root /包外/runs --run-id migration-001

python3 /完整包/skills/vllm-model-migration/skills/vllm-migration-execute/scripts/execute.py \
  --request /包外/finalize.request.json --work-root /包外/runs --run-id migration-review --action finalize
```

计划只检查顶层形状和安装包输出保护，不读取引用资料、不调用任务、不写运行目录。每次 run/finalize 新建独立 attempt；重新 run 会再次核验并调用所需任务，不承诺自动恢复或 exactly-once。发生取消或超时先通知自有 CLI 收敛；服务资源以原所有者回执为准，清理未确认时不得宣称成功。
