---
name: verify-agent
description: 在授权目标环境部署或核验 vLLM-Ascend 模型服务，复验最终载体注册、真实权重、NPU worker、文本/chat/完整 SSE 及所需图像功能，交付服务身份与证据。功能验证不替代正式精度或性能测试。
---

# vLLM-Ascend 部署与功能验收

接收显式模型资产、运行载体、源码/版本身份、设备、端点、启动配置、功能用例及保留策略。缺失会改变运行目标的输入先补齐；没有默认模型路径、TP、设备、端口、量化或 trust_remote_code。只执行用户已授权的部署范围。

客户流程通过本任务的 `scripts/customer.py` 发现主机、镜像源码及运行路径、计算容量。新部署及失败续跑先执行 `observe capacity=true`，再将本次观察引用与允许范围内设备交给 `allocate`；不使用 context 的旧空闲状态。同名且已退出的容器经精确身份及状态复核后自动非强制删除，其他状态保留绑定完整身份的删除授权。自动路径不额外询问用户；字段及调用见[客户观察契约](../../../../references/customer-observations.md)。实际服务启动、复核及停止仍只走本任务公开能力。

客户 allocate 自动指定 idle_first_shared，优先空闲卡，必要时按逐卡预算共享占用卡；独立未指定策略的请求保持独占。verify 通过可选 npu.allocation 绑定分配回执，两次启动前复核固定预算；运行后的复核允许同卡外部进程，但保持自有 worker 的完整归属检查。详见[共享部署契约](../../../../references/customer-observations.md#空闲优先与共享部署)。

## 执行步骤

1. 读取[请求与交接契约](references/contract.md)。消费准备清单、支持度及必要适配/注册证据，核对实际 config、完整权重和版本。独立调用可以提供等价显式身份，不冒充已有交接。
2. 将已批准的适配纳入最终载体。需要构建时固定整个 context 和 Dockerfile 内容；记录基础与实际 image ID。已有派生镜像须逐文件核对，不能把本地 diff 当作容器中已生效。配方方法见[运行说明](references/recipes.md)。
3. 在目标环境使用完整资源副本；远程执行沿用已授权连接，不另建 SSH 调度。不得要求安装维护后端。先执行 --plan，再执行本次 verify。
4. CLI 在最终载体先查询物理卡、逻辑卡和进程索引映射，再以同一有效环境执行运行检查、vllm-model-register 的公开 check.py 和服务启动。查询缺失或冲突必须阻断；不能固定将物理号写入 ASCEND_RT_VISIBLE_DEVICES，也不能自动换成 privileged。随后启动 owned 服务或核验 borrowed 服务，HTTP 200 不能代替真实模型功能。
5. 逐项查看原始响应、完整 SSE 和功能覆盖。图像必须有不同的真实图片输入。exact 仅用于预先确定的完整答案；开放语义使用 review，待复核时 success=false。读取完整文本及图像后，用绑定原始报告及响应 SHA256 的 review 请求记录逐项理由，不能修改原报告冒充初次成功。
6. 交付实际源码、镜像、资产、命令、载体、监听者、NPU worker、请求/响应和服务状态。成功默认 keep；需要临时停止时显式选择 stop。borrowed 不自动停止，资源不足不得清理其他任务。

```bash
python3 /实际技能目录/scripts/verify.py \
  --request /目标环境/verify.request.json --work-root /包外运行根 \
  --run-id 本次标识 --plan

python3 /实际技能目录/scripts/verify.py \
  --request /目标环境/verify.request.json --work-root /包外运行根 \
  --run-id 本次标识
```

公开入口另支持 --action review、--action stop 和只读 --action inspect-devices，沿用相同四个参数。详见契约及[输入模板](assets/request.example.json)。计划不读目标资产、构建镜像、启动服务或创建运行目录。实际执行的每次尝试写入独立目录。

## 证据与失败边界

- 共同结果 schema_version=1.0，保留 success、summary、detailed_report、report_path。success 来自本次所有必需检查；协议失败或语义待复核时不能为 true。
- 使用完整 container ID、image ID、StartedAt 或 Linux PID/starttime/进程组身份；新建容器额外绑定 owner nonce。不以名称、模型名、端口或主进程退出推导资源已释放。
- 在请求前后核对API与NPU worker的PID/starttime，变化时不能交付成功。Docker短检查采用独立任务身份及Linux subreaper边界，取消先清理自有派生树，再退出客户端，不停止borrowed模型服务。
- 每项用例独立保存失败和原始响应；输入、运行载体或注册前置失败会阻塞依赖项。取消和超时只清理本次拥有且身份未变的资源。禁止全局 pkill。
- 最终报告写入失败不能报告成功；输出诊断并保留已有证据。失败不自动换模型、升级环境或进入异常修复链。
- 无效usage归档为单用例失败；SSE实际用量参与prompt覆盖判断。三种操作均保护完整安装资源根。停止期间取消仅恢复一次有界清理，保存failed终态与真实资源结果，borrowed不停止。
- 本实现核验单机 Linux /proc 与 host network 的端口归属；其他网络方式先明确适用方法，不能绕过检查。正式数据集精度、性能、多机、音频/视频及其他未经实测组合分别保留为未验证。
