# 空闲优先与共享 NPU 实现记录

实现起点为本地 dev `8c3d92771c7cfd2bf2dd453396a77c8792276673`。功能提交 `1ec7ff88d023e74da46c653ca6640d349cc6469a` 已通过 `e1821142164b9462d9213bf8943ee3a3bb012443` 合入本地 dev，主题分支 `codex/shared-npu-capacity` 已删除。下文保留实施时的职责、RED/GREEN 和审查证据；当前验证数量统一见[验证基准](TESTING.md#当前验证基准)。Git 集成不代表真实共享部署已验收。

## 职责与变更形状

路径均相对总业务 Skill；测试相对仓库测试根。新增公共部分只承担静态预算和证据合同，不执行设备操作。

| 路径/单元 | 所有者、单一职责 | 消费者/边界与依赖 | 修改前行数/预期增长 |
| --- | --- | --- | --- |
| scripts/device_evidence.py | 文件引用与摘要绑定，原函数从 device_contract 平移并保留旧导出 | device_contract、allocation_contract；标准库 | 新增，21行，打断合同间循环依赖 |
| scripts/allocation_contract.py | 共享静态预算与分配回执校验 | verify、device_contract、交付；标准库/固定证据合同 | 新增，预计约200行 |
| verify/customer/shared_capacity.py | 空闲优先及共享候选选择 | 同任务 allocate；静态预算合同 | 新增，预计约100行 |
| verify/customer/capacity.py、npu_capacity.py、cli.py | 兼容原分配，绑定观察及结构化失败 | 公开 customer CLI；任务内依赖 | 73/57/63，少量增长 |
| verify/devices/allocation.py | 分配证据及创建/启动前动态复核 | runtime；同任务观察及公共静态合同 | 新增，预计约100行 |
| verify/runtime/inspection.py、carrier.py | 在已有生命周期边界委托预算及归属检查 | verify/inspect-devices；同任务 devices | 103/155，少量增长 |
| verify/devices/workers.py | 分离自有与外部进程 | runtime；不新增跨 Skill 依赖 | 54，少量增长 |
| verify/request.py、runner.py | 可选回执形状和结构化诊断传递 | 公开 verify；委托领域模块 | 287/90，少量增长 |
| scripts/customer_workflow/compile/deployment.py | 生成共享分配请求及一致 argv | 客户 runner；公开 verify 文件合同 | 158，少量增长 |
| scripts/customer_workflow/effects.py、scripts/skill_paths.py | 保存失败操作回执、声明新合同必需资源 | 现有公开委托及资源枚举 | 113/227，少量增长 |
| scripts/customer_workflow/recovery.py、runner.py | 容量不足转用户决策，独立准备继续 | 客户流程；公开回执 | 117/255，少量增长 |
| scripts/device_contract.py | 绑定共享证据至原声明及导出 | 精度/交付；静态合同 | 116，少量增长 |
| scripts/tuning_handoff/deployment.py | 配置身份排除回执位置，只比较语义 | 三文件交付；静态合同 | 现有模块，少量增长 |
| tests/vllm-model-migration/devices/test_shared_*.py | 共享预算、运行与消费者回归 | 标准库；外部效果模拟 | 新增，分文件控制600行软阈值 |

verify 路径指 `skills/vllm-migration-execute/skills/verify-agent/scripts/verification`。保留三层公开 CLI 边界；总入口不导入 verify 私有实现。静态配方保持冻结，分配回执携带动态实际比例。task.json 与三文件结构不增字段，旧无策略请求保持独占。

修改前结构审计：0 errors、174 warnings。函数复杂度、分支和语句使用现有 `.codex/code-structure-baseline.json` 的起点指标；参数数在实现前后通过 AST 逐项核对。不提高硬热点或阈值。

## 失败边界与任务台账

| 步骤 | 前置/产出与依赖者 | 失败归属/恢复 | 完成状态 |
| --- | --- | --- | --- |
| 失败测试 | 已批准策略；保护分配、启动、复核与用户决策 | 本地测试；记录实际 RED | completed：已记录 RED/GREEN |
| 分配与预算 | 本次有效观察、允许池、模型配方；产出绑定回执 | 部署分支；容量不足为用户更换卡决策，TP/观察错误单列 | completed：离线通过 |
| 创建/启动 | 分配回执、同主机及最新容量；产出自有资源 | 部署分支；资源变化不改 argv、不重试，不清理其他任务 | completed：离线通过 |
| 运行验收/复核 | 自有 worker、设备映射与声明；产出交接 | 部署/精度消费者；外部进程仅观察，自有身份变化仍阻塞 | completed：离线通过 |
| 用户决策 | 结构化 capacity_insufficient；产出 owner=user | 仅阻塞部署与其消费者，独立数据准备继续 | completed：离线通过 |
| 验证/审查/文档 | 完成实现；定向、全量、静态及结构证据 | 独立检查失败各自保留，修复后重跑相关项 | completed：离线通过 |

共享组合每张卡预留 ceil(当前剩余HBM/20)，与模型运行开销分开。全空闲优先；共享按占用卡数、TP、卡号排序。实际比例不超过配方上限和任一卡可用比例。启动前复核固定预算；运行后不拿已消耗的空闲显存再做启动门禁。分配观察的60秒时效仅用于新分配，历史证据复核不把已启动服务当作新的分配。

## 验证与审查记录

### 具体原因与变更

原实现有三层独占限制：`verify/customer/capacity.py::select` 在容量计算前过滤 occupied 卡；`verify/runtime/inspection.py::available` 看到计算进程就拒绝创建；`verify/devices/workers.py::select_workers` 在运行验收时拒绝同卡外部任务。只改选卡无法完成部署链路。没有硬编码“必须两张卡”；TP 来自模型/版本的 allowed_tp。最初离线复现确认容量计算本可通过，但占用过滤导致无可用组合。这定位的是源码与合成用例，用户没有提供本次服务器日志。

本次新增可选策略及公共静态预算合同，客户自动启用，独立旧请求保持独占。实际比例同时绑定 allocation、verify argv 和三文件导出；npu.allocation 只引用经过校验的回执。共享状态不改写原始 task.json，也不扩大允许池或清理其他任务。创建/启动前各采集一次，运行验收、inspect-devices 和精度前后复核继承共享策略。

### RED / GREEN 与验收对应

先补 `test_shared_capacity.py` 和 `test_shared_runtime.py`，在旧实现上复现 policy 参数不被接受、独占启动/worker 门禁及用户决策缺失，再实现。原14项容量回归先行通过，证明不是既有测试环境故障。实现后的首轮全量曾有2失败/3错误，均定位为旧客户模拟未保存分配回执、mock 委托边界及新增静态模块被误作 CLI；调整夹具/公开入口枚举后保留旧独占断言。

| 批准的验收点 | 离线证据 |
| --- | --- |
| 空闲足够优先，即使共享 TP 更小 | shared_capacity 的 idle_combination_beats_smaller_shared_tp |
| 混合组合按占用数/TP/卡号排序 | mixed_combination_uses_fewest_occupied_then_smallest_tp |
| 0–3全占用，TP2不足、TP4满足 | all_four_occupied_selects_tp4_when_tp2_does_not_fit |
| 5%预留导致临界不足且保留缺口 | shared_reserve_is_extra_and_can_make_boundary_insufficient |
| 总容量不能掩盖最弱分片 | sum_of_free_memory_cannot_hide_weakest_shard |
| 向下比例、argv及导出一致，覆盖配置拒绝 | heterogeneous_cards、customer_compiler、shared_three_json_export |
| 创建或启动前容量下降/原空闲变占用 | shared_runtime 的 capacity_drop、idle_allocation 测试；effects 未调用 |
| 外部任务共存，自有缺失/越界/身份变化失败 | worker_partition、external_process_exit、foreign_identity_churn；既有设备身份回归 |
| 精度前后公开复核、三文件导出、只清理自有资源 | shared_flow 的5项集成回归；共享复核不调用容量观察/启停 |
| 用户决策、独立准备、旧请求 | capacity_failure_is_user_decision、stage_capacity_diagnostic、legacy_stays_exclusive；客户续接回归 |

新增共享专项27项（分配10、运行12、流程5），全量从363增至390项。所有新数值、PID、容器和设备均为离线合成输入或边界替身；实际文件摘要、编译、分配、inspect-devices 逻辑、精度设备检查及三文件生成/联合校验/发布在测试中执行。

### 实际检查结果

```bash
python3.12 -B -m unittest discover -s tests/vllm-model-migration/devices -p 'test_*.py' -q
python3.12 -B -m unittest discover -s tests/vllm-model-migration -p 'test_*.py' -q
python3.12 -B .agents/skills/codebase-organization-guardrails/scripts/source_shape_audit.py \
  --repo-root . --policy .codex/code-structure-policy.toml \
  --baseline .codex/code-structure-baseline.json
git diff --check
```

- 设备专项：78项通过；完整回归：390项通过，无跳过。完整回归包含搬迁后的公开入口、客户冻结/续接和旧回执兼容检查。
- 新增合同缺失专项：资源发现13项通过；先在缺失 allocation_contract/device_evidence 时复现未拒绝，再将其登记为必需资源。
- 按 [TESTING.md](TESTING.md#静态语法检查) 的静态检查：249 Python、42 JSON、2 shell 通过。20个公开入口 --help 均退出0；开发技能快照12个技能/37个资源、4 Python/1 shell及依赖闭包通过。
- 治理文档检查曾发现 verify 契约的共享文档相对链接少一层，已修正；最终75份文档、299文件结构基线解析通过，0错误。git diff --check 退出0。
- 结构审计：0 errors、180 warnings（起点174）。14项原始阻塞均为要求收紧基线；只登记7个新增源/测试文件及其函数、缩小的文件/函数并移除已迁出的3个函数记录。已有目录/函数/文件的增长指标不提高基线，策略及硬阈值不变。

### 审查发现与处理

未留下阻塞或可执行缺陷。以下问题均在本轮补回归并修复：

1. 外部进程在快照后退出或身份变化会误报服务失败：只在共享选择中容忍已消失的进程，不吞权限/身份错误；仍要求每张卡自有 worker，归属已确认的自有 PID 继续双次身份检查。两项测试先RED后GREEN。
2. 非对象回执/inputs/spec 导致 AttributeError：边界增加形状验证；三种畸形证据先RED后GREEN，效果前拒绝。
3. 静态合同相互导入：将原摘要函数平移为 device_evidence，保留 device_contract 旧导出，依赖改为单向；搬迁/公开入口/旧合同回归通过。
4. 新增必需合同被遗漏也可枚举发布包：补必需资源声明及缺失回归，保持动态资源发现。
5. 容量不足候选的理由误写“满足容量”：逐卡预算拒绝时改为明确不足，新增断言先RED后GREEN；成功分配及其绑定预算不变。

软提示逐项审查：总 scripts 9→11个直接文件，两个新文件分别负责预算合同与底层摘要绑定，有实际多方消费者且避免依赖环；设备测试目录9→12按预算、运行、完整消费者分开。receipt 与 candidates 复杂度均15，分别集中完整回执绑定及确定性组合排序；没有副作用或泛化框架。request.load 和 running_evidence 各9→12，增长来自可选回执形状及外部进程分离，仍由原入口拥有；runner.run 语句58→59只传递结构化诊断，属于既有软提示。没有硬热点增长或阈值豁免。

### 未验证范围与交付边界

实际四卡共享模型启动、驱动/容器允许多任务共享、CANN 映射、真实功能和精度结果尚未验证。预算依赖已核验的模型分片/KV/运行开销配方；5%余量及两次观察不构成设备锁，启动后仍可能受到其他进程影响。无需为本次离线交付操作服务器。

实现及验证没有操作服务器、安装依赖或使用子代理。原 task.json 模板和三份交付 Schema 不变，历史结论不改写。


### 源码指标复核

| 模块 | 行数（前→后） |
| --- | ---: |
| scripts/allocation_contract.py | 新增→148 |
| scripts/device_evidence.py | 新增→21 |
| scripts/device_contract.py | 116→100 |
| scripts/customer_workflow/compile/deployment.py | 158→159 |
| scripts/customer_workflow/recovery.py | 117→139 |
| scripts/customer_workflow/runner.py | 255→256 |
| scripts/customer_workflow/effects.py | 113→115 |
| scripts/skill_paths.py | 227→228 |
| scripts/tuning_handoff/deployment.py | 119→120 |
| verify/customer/shared_capacity.py | 新增→35 |
| verify/customer/capacity.py | 73→79 |
| verify/customer/npu_capacity.py | 57→60 |
| verify/customer/cli.py | 63→70 |
| verify/devices/allocation.py | 新增→57 |
| verify/devices/workers.py | 54→60 |
| verify/runtime/inspection.py | 103→108 |
| verify/runtime/carrier.py | 155→157 |
| verify/request.py | 287→293 |
| verify/runner.py | 90→91 |

| 函数 | 复杂度 前→后 | 分支 前→后 | 语句 前→后 | 参数 前→后 |
| --- | ---: | ---: | ---: | ---: |
| scripts/customer_workflow/runner.py::migrate | 7→7 | 5→5 | 30→32 | 6→6 |
| verify/customer/capacity.py::select | 6→8 | 2→4 | 8→14 | 3→4 |
| verify/customer/npu_capacity.py::bound_devices | 6→8 | 0→0 | 16→17 | 1→2 |
| verify/devices/workers.py::select_workers | 5→8 | 3→5 | 10→14 | 3→4 |
| verify/request.py::load | 9→12 | 3→5 | 18→24 | 1→1 |
| verify/runtime/inspection.py::running_evidence | 9→12 | 4→4 | 19→20 | 3→3 |
| verify/customer/cli.py::main | 10→9 | 11→9 | 38→38 | 0→0 |
| scripts/allocation_contract.py::receipt | 新增→15 | 新增→0 | 新增→19 | 新增→1 |
| verify/customer/shared_capacity.py::candidates | 新增→15 | 新增→3 | 新增→8 | 新增→3 |
| verify/devices/allocation.py::before_start | 新增→4 | 新增→2 | 新增→14 | 新增→3 |
