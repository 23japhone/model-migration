---
name: vllm-model-prepare
description: 适用于为华为昇腾上的 vLLM-Ascend 准备模型元数据或完整模型资产，支持 Hugging Face、ModelScope 和本地目录，可下载或复用资产，并核验文件身份及权重、tokenizer、processor 的完整性。
---

# vLLM 模型资产准备

为后续 vLLM-Ascend 选版、支持度预检和部署交付可访问的模型资产与核验清单。脚本可在资产所在环境独立执行，不启动模型服务，不证明模型已支持昇腾或推理已通过。

客户流程先使用 `scripts/resolve.py --model` 读取客户 model 对象：发现完整本地权重且提供了仓库 ID 时返回 `local_identity_review_required`，由 Agent 核对来源身份并按[客户输入契约](../../../../references/customer-task.md)提交复核，一致后才复用。身份冲突须确认，不忽略仓库 ID，不自动下载或覆盖已有完整权重。未指定仓库 ID 时仅核验本地内容；缺完整权重时，ModelScope 确认不存在后才查 Hugging Face，网络及鉴权失败不换源。resolver 的 resolved 只表示发现完成，不表示客户身份门禁已经通过。本入口不下载；下面独立准备入口原有显式来源和代理处理仍保留。

## 输入与范围

先确定资产所在环境、来源、路径和本次范围；完整准备还需明确 `task` 与所需模态。用户只提供官方模型链接时，可使用对应来源并解析版本；不要把 SDK 的默认行为当作已固定的身份。

| 范围 | 含义 | 完成条件 |
| --- | --- | --- |
| `metadata` | 配置、processor 资料、模板及权重索引；不下载权重或大型 tokenizer 文件 | 选中的资料可读、来源身份与清单齐备；不能交接成完整模型 |
| `full` | 完整仓库下载或本地完整资产核验 | 配置、tokenizer、任务所需模板及 processor、索引和分片检查通过，内容身份已留存 |

完整下载保留仓库全部文件，可能包含多个权重格式；执行前说明下载范围、目标存储及容量。已有文件只在身份核对后复用，冲突文件保留原位，不覆盖、不自动删除。HF 因代理问题不可用且用户没有可用代理时，按下述规则改用 ModelScope，使用独立请求和资产目录。其他来源或内容冲突不触发换源；特殊权重或 tokenizer 格式不猜测为可用，按脚本诊断交付。

## 执行

1. 阅读 [请求与输出契约](references/contract.md)，按用户任务生成 JSON；可从 [本地示例](assets/local-full.example.json)、[HF 示例](assets/huggingface-metadata.example.json)、[ModelScope 示例](assets/modelscope-metadata.example.json) 修改。
2. 先用 `--plan` 核对参数和路径。它没有网络访问或写入，也不能证明路径存在、SDK 可用或资产完整。
3. 在资产所在主机运行随附脚本，入口为 `<技能资源目录>/scripts/prepare.py`。调用方与资产主机不同时，将本技能所需文件完整传到已授权目标的新目录并核对摘要；路径以实际执行主机为准，不把本地资源路径当作远程路径。
4. 下载时使用已安装官方 SDK，参考 [来源与版本](references/sources.md)。缺依赖时报告阻塞，给出隔离环境安装方式；不隐式升级全局 Python、安装额外业务服务或配置 SSH。
5. Hugging Face 或 ModelScope 下载出现网络错误时，按[网络异常与代理](references/sources.md#网络异常与代理)先核对原因，不能仅凭一次超时认定代理故障。确认是代理问题后，先询问用户是否有可用代理；有则请用户提供代理地址，在实际下载进程中配置后继续原模型准备任务，凭据仅留在授权环境。用户没有可用代理时，停止 HF 下载，按[无代理时改用 ModelScope](references/sources.md#无代理时改用-modelscope)准备对应模型，不反复要求用户配置 HF 代理。不把单独的 HF 网络可达性验收作为前置任务；用户已回答的代理情况不重复询问。
6. 阅读标准输出指向的清单，核对 `run_id`、所在主机、实际路径、来源、范围、内容身份和结论。失败仍交付诊断与已有证据。后续尝试使用独立证据目录，旧记录不被覆盖。

```bash
python3 "/实际技能路径/scripts/prepare.py" \
  --request "/实际请求路径/model.json" \
  --work-root "/用户指定证据根" --run-id "customer-run-001" --plan
# 核对后去掉 --plan 执行本次已授权的准备。
```

## 判定与交接

`status=completed` 且 `conclusions.assets=pass` 仅表示请求范围内的资产检查完成。元数据清单保留 `scope=metadata`；完整模型的消费者必须要求 `scope=full`。`runtime_support` 始终为 `not_evaluated`，后续由支持度预检与实际部署分别确认。

本地内容摘要不能证明上游来源一致；ModelScope 分支名也不是不可变的 repo commit。GPU/NPU 应核对同一文件清单与内容摘要，不能仅比较模型名称。Safetensors 只检查头部、索引和文件结构，`.bin` 不反序列化；没有执行权重加载或推理。

任务结果使用随包的[组级结果 Schema](../../../../references/task-result.schema.json)。静态资源归总入口维护；本任务不调用总入口执行。将清单绝对路径交给调用者，不另行开发镜像、数据集、评测或诊断能力。
