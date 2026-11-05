# NPU 设备编号映射实现记录

起点：本地 dev c5ddf778f091a657b35f6a503124f95d651dbad9。仅来源仓迁移能力；原始请求冻结、运行环境派生；最终载体 CANN 映射和宿主全设备进程共同验收。

首版提交为 `b361129`，经 `3104da6` 合入本地 dev；原主题分支和开发 worktree 已完成清理。本文保留首版职责、失败边界及验证记录，其中测试和结构数量属于当时版本。现行行为以[设备验证契约](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/contract.md#设备编号与有效运行环境)为准，后续修复及当前验证数量见[验证基准](TESTING.md#当前验证基准)；Git 集成不代表真实设备验收。

## 实现前 Change Shape Map

verify 表示业务根内 verify-agent/scripts/verification，precision 表示 verify-agent-plus/scripts/precision；下列新模块均保持单一能力，目标低于400行。

| 路径 | 所有者/单一职责 | 当前消费者/边界 | 允许依赖/预期增长/放置依据 |
| --- | --- | --- | --- |
| scripts/device_contract.py | 共享设备证据文件契约 | verify、交接导出；公开静态合同 | 标准库；约200行；跨Skill共享固定文件校验 |
| scripts/skill_paths.py | 静态资源必需项 | 注册复制与搬迁；公开路径合同 | 将设备证据合同列为必需项；不增加业务调用 |
| verify/devices/policy.py | 映射选择纯规则 | 映射编排；私有 | 静态合同；约80行 |
| verify/devices/probe.py | 独立CANN ctypes探针 | 最终载体短任务；私有 | 标准库；约160行；不加载模型/设卡分配 |
| verify/devices/inventory.py | 宿主NPU/Chip编号解析 | worker检查；私有 | 标准库、命令效果；约100行 |
| verify/devices/workers.py | 全设备进程归属与环境取证 | runtime、复核；私有 | /proc、既有进程身份；约120行 |
| verify/devices/mapping.py | 探测、派生和保存映射证据 | carrier；私有 | 同包规则、既有短任务执行；约200行 |
| verify/devices/recheck.py | 公开只读检查动作组装 | CLI、precision；公开CLI | carrier、文件合同；约100行；不启停服务 |
| scripts/customer_workflow/compile/deployment.py | 编译物理卡意图 | 客户流程；私有 | 原依赖；移除物理号填RT |
| verify/runner.py | 组装映射前置 | verify；私有 | carrier；不解析映射 |
| verify/runtime/carrier.py | 映射和服务交接 | runner；私有 | devices；维持生命周期唯一所有者 |
| verify/runtime/execution.py | 向短任务注入有效环境 | 探针、注册；私有 | 同runtime命令；约15行增长 |
| verify/runtime/docker.py | 创建物理设备容器并注入启动环境 | carrier；私有 | execution；约10行增长 |
| verify/runtime/inspection.py | 全设备归属验收 | carrier；私有 | devices；缩小已有热点 |
| verify/cli.py | inspect-devices参数路由 | 公开入口 | 同Skill动作实现；少量路由 |
| precision/runtime/device_check.py | 公开CLI委托与回执核对 | service；私有 | skill_paths、固定回执；约60行 |
| precision/runtime/service.py | 正式精度前后快照 | precision；私有 | 公开CLI；删除重复NPU筛选 |
| scripts/tuning_handoff/provenance.py | 验证有效环境来源 | 导出；私有 | 公开device_contract；原声明保持不变 |
| scripts/tuning_handoff/deployment.py | 候选配置校验 | 导出；私有 | 公开device_contract；只更改环境来源 |
| scripts/tuning_handoff/generation.py | 输出有效环境 | 三文件；私有 | 公开device_contract；少量替换 |
| tests/vllm-model-migration/devices/ | 设备行为专项回归 | unittest；测试 | 模拟外部效果；按能力分组 |

实现前结构审计0 errors、173 warnings。相关文件行数：compile/deployment 125、verify/runner 85、runtime/docker 109、execution 23、inspection 97、precision/runtime/service 213、tuning_handoff/provenance 123、generation 85。running_evidence复杂度12；runner.run语句54；新增判断放入devices包。完整函数/目录指标可由起点结构基线复现。

## 失败边界与任务台账

- 映射是注册、启动和运行验收的必需依赖；失败保存探针结果，阻断消费者，由既有生命周期只清理owned资源。
- 只读复核失败影响消费者本次有效性，不停止已有服务，不抹去其他准备/评测结果。
- 导出校验失败只阻断发布，不改写原请求、部署、精度证据。
- 首版实现、专项/全量/静态/公开CLI/文档/结构检查及最终差异审查 succeeded；集成状态见首段，实机验收单独判断。
- 首版实现轮的实机NPU验收 pending：当时没有连接服务器、创建容器或运行模型。2026-11-03后续续跑曾取得设备探针通过，但未完成有效部署交接；用户随后要求省略探针在2、3号卡直接部署，其功能与性能基线不替代完整映射链验收。当前状态及版本差异见[续跑记录](TESTING.md#2026-11-03-qwen35-27b-断点续跑与直接部署)，Git集成仍不代表实机验收。

## 实现与证据

客户编译器保留物理选卡并设置 ASCEND_VISIBLE_DEVICES；最终载体内两个独立 CANN 探针分别取得基础映射和有效环境映射，才生成逻辑号 ASCEND_RT_VISIBLE_DEVICES。独立调用的显式 RT 不静默改写。Docker 探针、注册与服务启动经 docker exec --env 传递同一有效环境；原始请求和 handoff.runtime 保持原声明。

宿主 npu-smi info -m 保留 NPU/Chip 关系，info 扫描全部设备计算进程。验证完整容器 ID/cgroup 或进程组、PID/启动时间、实际解释器及加载的 CANN 库；选中卡有外部进程、本服务越界用卡、缺少 worker 均阻断。功能请求前后再次取证，并在探针结束后复核进程身份。首版只接受能明确解析的单机 Linux Ascend 910B 整卡；未知布局阻断，不从设备文件名或排序推断映射。

精度阶段通过公开 inspect-devices 动作复用同一设备校验。只读动作输出独立回执，不停止 borrowed/owned 服务。精度前后比较稳定的映射、环境和进程身份，检查回执位置与原始 stat 计数另存，不把这些必然变化的观察字段当成服务变化。导出验证摘要链与原始请求，只允许经过探针核验的设备变量派生，三个外部 JSON 的结构保持不变。

device-mapping.json、effective-runtime.json 及原始探针通过 SHA-256 互相绑定，并绑定宿主 boot ID、载体身份、CANN 库路径/内容/版本和解释器。更换载体后不能沿用；旧交接缺少这些证据时必须重新验证。证据是某次观察及文件完整性记录，不是数字签名，也不保证两次观察之间绝无瞬时用卡变化。导出只读已有证据，不证明服务在导出时仍存活。

## 验证与审查

- RED：首批8项中7项复现缺失能力、1项复现 Docker 未显式注入环境；随后分别复现导出仍取原始环境、精度快照因回执位置变化而误判、旧交接未返回 blocked 回执，以及复核遗漏新增设备变量。修复后全部通过。
- 设备专项34项、全量312项通过，无跳过；控制 Python 3.12.12。全量包含中文/空格路径搬迁、公开计划和原有客户/数据/注册/精度交接回归。ctypes、Docker、宿主进程和 npu-smi 使用模拟观察，不能作为真实设备验收。
- 静态语法242 Python、42 JSON、2 shell；20个公开 CLI help；12个开发技能、37项资源依赖；74份文档检查通过。git diff --check、只读工作区盘点通过。
- 最终审查修复了精度快照比较、旧证据失败回执、最终 PID 复核和新增设备变量漏检；未留下已知阻塞缺陷。未新增依赖、安装工具或改变外部性能插件。实现验收与后续 Git 提交、集成分别记录，不由代码检查推导部署状态。

普通回归命令见[验证文档](TESTING.md)，设备专项可单独运行：

```bash
python3 -B -m unittest discover -s tests/vllm-model-migration/devices -t tests/vllm-model-migration -p 'test_*.py'
```

结构审计从0 errors/173 warnings变为0 errors/174 warnings；284文件、45目录、1788个函数。新增设备能力集中在devices目录，测试也按设备能力归组；没有增加原测试根的直接文件数。原 runtime/inspection.running_evidence 复杂度12→9，precision/runtime/service 213→200行，删除重复NPU判断。基线由工具更新并收紧改善项，没有修改阈值或批准硬基线上调。

共享合同的 load 复杂度14（19条语句）、runtime_change 复杂度11（6条语句）分别集中校验同一证据链和运行环境不变式，保留短函数以便逐条审查；原 provenance.deployment_configuration 复杂度11保持不变。runner.run语句54→58，仅增加映射前置及原请求副本，已有阶段编排软提示保留。未新增硬热点，新增函数参数均不超过5个；既有生产函数的参数数量不变，跨Skill仍使用公开CLI或共享静态合同。
