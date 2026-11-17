# msProbe 运行时采集交付

本次拆分仅交付 `verify-agent` 管理的运行时采集工具；完整使用说明、输入输出及限制唯一维护在[工具说明](../skills/vllm-model-migration/skills/vllm-migration-execute/skills/verify-agent/references/runtime-capture.md)。无需 GPU 基线解析或评测 SDK，调用方可以直接控制单条服务请求。

## 代码归属

| 模块 | 职责 |
| --- | --- |
| `verification/request.py`、`runner.py`、`lifecycle.py` | 可选采集准备、部署回执及最终服务交接 |
| `verification/runtime/capture/contract.py`、`prepare.py`、`probe.py` | 模式和配置检查、挂载映射、目标解释器探针及读写证明 |
| `capture/launch.py`、`adapters/` | 启动适配器及实际前端、引擎、worker 挂点；独立复制 logits/分数 |
| `capture/service.py`、`lease.py`、`session.py`、`session_receipt.py` | 部署与进程身份、互斥、逐请求授权及封存 |
| `capture/index.py`、`tensors.py`、`action.py` | 原生调用索引、覆盖和摘要校验、独立诊断与离线查询 |
| `tests/vllm-model-migration/worker_capture/` | 准备、适配器、会话、覆盖与不依赖评测器的公开闭环 |

普通部署不声明 `worker_capture` 时兼容原契约。采集准备不可用单独报告，不掩盖普通功能失败；取消和最终证据写入失败沿用原资源清理规则。采集成功不等于精度对齐或性能达标。

## 2026-11-17 离线验收

专项命令：

```bash
python3.12 -B -m unittest discover -s tests/vllm-model-migration/worker_capture -p 'test_*.py' -v
```

42 项通过。新增 9 项公开闭环测试在完整资源的临时副本中运行真实 CLI、生产部署交接校验、一次本机 HTTP 请求、封存和离线查询。仅目标 Linux 进程观察与实际 worker 推理由显式替身替代；评测 SDK 导入被拒绝。覆盖第二个输出 Token、不同实际 ID、完整词表、全部合成层、重复 API、重复 Token、无输出前向、提前 EOS、漏采、摘要篡改及进行中的请求租约。已有测试另验证实际适配器逻辑与 Docker/process 准备交接。

全量离线回归 476 项、source_contract 专项 52 项通过，均无跳过。结构审计为 0 errors、194 warnings，原 dev 为 177 warnings。本次增加的软提示对应有界采集校验函数及两个目录；最高复杂度 15，未超硬阈值。请求/身份/调用覆盖校验分别保留在所属边界，未增加跨模块调度或共享杂项包。基线由现有生成器登记新增文件并收紧 cleanup 指标；未修改策略、阈值或添加豁免。

原始改动中的 GPU 基线解析、NPU 按基线复测、EvalScope 控制器、评测样本映射和其相关契约均不在本提交。测试替身不证明真实模型/NPU、任意版本、图模式、多 rank、并发或设备 kernel 覆盖；本轮未运行服务器任务或安装依赖。
