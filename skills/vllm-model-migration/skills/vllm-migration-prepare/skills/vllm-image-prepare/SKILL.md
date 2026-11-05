---
name: vllm-image-prepare
description: 适用于为华为昇腾上的 vLLM-Ascend 准备 Docker 镜像，支持已有镜像复用、按 digest 拉取和 Docker save 归档加载，核验 image ID、digest、平台及硬件版本配方并保存交接证据。
---

# vLLM-Ascend 镜像准备

将已选定的镜像准备到目标 Docker 环境，交付实际身份、来源和平台。镜像准备完成不等于框架支持、驱动兼容或 NPU 推理通过；本技能不创建服务容器。

## 输入与完成条件

先确定目标 Docker daemon、昇腾芯片及产品系列、平台和来源。按对应版本的官方资料选择 vLLM-Ascend 配方，记录配套 vLLM 版本和资料链接；不要按卡数猜产品系列，也不要把示例版本作为默认值。阅读[版本与来源规则](references/sources.md)，尤其核对不同页面的版本差异。

| 来源 | 输入 | 行为 |
| --- | --- | --- |
| `reuse` | 明确镜像引用及预期 image ID 或 repository digest | 只核验目标现有镜像，缺失即失败 |
| `registry` | 明确镜像引用和预期 repository digest | 已有镜像通过后复用；缺失时按 digest 拉取；已有冲突不覆盖 |
| `tar` | Docker save 归档、预期 image ID，可附归档 SHA256 | 检查归档和标签后加载；相同镜像已存在时复用 |

完成条件为 `status=completed`、`conclusions.assets=pass`，实际身份和平台满足请求。`source=review_required` 表示只有来源声明，不能作为已核对的官方来源交接；`hardware_compatibility=review_required` 表示需人工复核硬件和版本资料。两者不能被资产通过掩盖。`runtime_support` 始终未评估。

## 工作流程

1. 阅读[请求与输出契约](references/contract.md)，使用[复用示例](assets/reuse.example.json)、[registry 示例](assets/registry.example.json)或[tar 示例](assets/tar.example.json)生成请求；将示例身份、硬件、版本、平台和路径替换为实际值。按用户已有授权核对目标连接，不安装 Docker 或改写认证配置。
2. 用 `--plan` 核对请求。计划模式不调用 Docker、不联网、不读归档内容、不写运行文件，不能证明镜像存在或配方已匹配。
3. 在目标 Docker 执行环境运行 `<技能资源目录>/scripts/prepare.py`。调用方与资产主机不同，将完整技能资源传到已授权目标的新目录并核对摘要；路径和 daemon 以实际执行环境为准。
4. 运行前确认容量、网络和稳定的镜像变更窗口。tar 仅支持一份 Docker save 镜像，拒绝多镜像、链接、特殊成员、不完整层和冲突标签；不向主机目录解包。registry 拉取以 digest 固定内容，不重新指向已有 tag。
5. 阅读标准输出指向的 `image-manifest.json` 和命令证据，核对实际 daemon、请求身份、平台、操作、来源和未覆盖项。发生失败时保留已有镜像、部分层及诊断；Docker CLI 超时不证明 daemon 已停止拉取或加载，复查实际状态后再重试。

```bash
python3 /实际技能路径/scripts/prepare.py \
  --request /实际请求路径/image.json --work-root /用户指定证据根 \
  --run-id customer-run-001 --plan
# 核对后去掉 --plan，执行本次已授权的准备。
```

## 交接

结果使用随包的[组级结果 Schema](../../../../references/task-result.schema.json)。证据保存在包外，每次执行创建新尝试目录，交付清单绝对路径。image ID、repository digest 和归档 SHA256 的含义不同，不能相互替代。

镜像来源可由目标 Docker 的 RepoDigests 记录核对；tar 和派生镜像可能丢失上游 digest，此时保留来源待复核，需提供原始发布或构建证据。版本配方来自已复核资料，不从 tag 猜测实际安装依赖。本任务不选择模型、运行服务、修改 CANN/驱动或停止既有任务。
