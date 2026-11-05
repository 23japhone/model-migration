---
name: vllm-model-register
description: 适用于在固定 vLLM/vLLM-Ascend 开发环境中为已有模型实现建立或复用 architecture 映射，生成 out-of-tree plugin，或在实际运行载体中复验注册、lazy import 和实现身份的场景。
---
# vLLM 模型注册

将原始模型 architecture 映射到已有 module/class。支持源码注册和 out-of-tree plugin，交付最小 diff 或标准 wheel、开发环境独立初检和可在部署载体复用的只读检查入口。注册与 import 不证明模型适配、权重加载、NPU 推理或服务功能。

## 工作流程

1. 阅读[请求与结果契约](references/contract.md)及[目标版本机制](references/sources.md)。明确原始 config、已有实现文件及 SHA256、目标 registry 文件及 SHA256、开发解释器和明确的 import_roots。已有预检回执必须验证摘要、终态、所选 architecture 和当前 config 身份；不能用 blocked 或改名 config 掩盖实现缺口。
2. 检查目标实现和允许执行的 plugin 的 import 副作用。目标 SDK 可能在 import 时初始化平台或查询芯片架构，实际调用前核对该环境已有的授权和可用资源；本 Skill 不选择或清理设备。实际检查在本次拥有的短进程中 import 授权代码，不实例化模型、不加载权重，不启动或停止服务；远程环境运行完整资源副本，不把本机绝对路径用于另一台主机。
3. 从[源码样例](assets/source.request.example.json)或[plugin 样例](assets/plugin.request.example.json)填写请求。源码方式只允许指定 registry.py 的映射修改；plugin 方式生成 lazy callback 和标准 wheel，仅用现有 runtime pip 安装到显式的隔离 site，不自动安装 SDK、构建依赖或改写全局环境。已有映射相同则复用，不同则阻塞，不能覆盖。
4. 使用实际资源路径执行 register.py --plan。它只检查请求、共同静态资源和输出保护，不读模型/目标源码、不 import、不安装、不写运行目录。去掉 --plan 后才执行注册与开发初检。初检失败保留原尝试、已执行变更和诊断，不自动回滚、重试、提交或发布。
5. 读取注册清单、diff/wheel 身份、实际版本、registry/class 路径和摘要、plugin entry point 与加载状态，以及独立进程日志。plugin 在 loader 调用前核对 Python 实际解析路径，加载后复核真实 module/register 函数的路径和摘要；同名遮蔽、预加载的其他 module 或加载后身份变化均阻塞。只有全部必需检查通过才 completed。生成文件、pip 退出 0、发现 entry point 或当前进程的临时注册，都不能代替独立检查。
6. 交付 registration-check.request.json 和检查入口。将改动纳入最终运行载体后，以该载体的实际路径/解释器重建等价检查请求，并从载体内调用 check.py。它不修改源码或 plugin，只在包外写证据及隔离缓存；源码摘要、实际 import 路径、plugin 未加载或 class 不一致均阻塞。开发环境成功不能关闭部署复验缺口。

```bash
python3 "/实际技能目录/scripts/register.py" \
  --request "/实际请求目录/register.json" \
  --work-root "/包外证据根" --run-id "register-001" --plan

python3 "/实际技能目录/scripts/check.py" \
  --request "/实际产物目录/registration-check.request.json" \
  --work-root "/包外证据根" --run-id "carrier-check-001"
```

报告分开列出真实操作、受控/离线检查和未验证项，保留原始 architecture 和模型资料。仅覆盖模型 architecture 注册，不注册量化算法，不修改 forward、权重映射、processor 或平台实现。服务与设备生命周期由后续部署任务负责；本 Skill 不承担 SSH/Docker 调度，不依赖维护后端。

短检查/安装命令通过 start_new_session 建立自有进程组。正常退出、超时、取消和证据失败均结束组内残留进程；主进程退出不代表派生进程结束。清理采用有界 SIGTERM/SIGKILL，拒绝 PID 或进程组身份变化，不使用全局 pkill。脱离原组的第三方进程不自动清理；清理无法确认时保存失败，不能交付成功。

安装/搬迁须保留[共同 1.0 Schema](../../../../references/task-result.schema.json)。成功清单仅证明本次声明环境中的注册和 class import；运行载体复验、真实权重和功能验收仍独立记录。
