# 官方资料、镜像来源与硬件配方

核对日期：2026-10-13。选用与实际芯片、产品系列和目标版本对应的 vLLM-Ascend 镜像；版本不设通用默认值。

| 一手来源 | 对本技能的用途与边界 |
| --- | --- |
| [vLLM-Ascend 0.18.0 安装](https://docs.vllm.ai/projects/ascend/en/v0.18.0/installation.html) | Docker 镜像按 Atlas A2、Atlas A3、Atlas 300I 区分；不能用卡数选择系列。页面列出 Ubuntu/openEuler 变体 |
| [同版版本矩阵](https://docs.vllm.ai/projects/ascend/en/v0.18.0/community/versioning_policy.html) | 配套 vLLM 与依赖版本依据；矩阵随资料更新，不将 tag 当作全部依赖安装证据 |
| [官方 Dockerfile 源码](https://github.com/vllm-project/vllm-ascend/blob/v0.18.0/Dockerfile) | 复核构建输入、基础环境及版本；不在准备技能内自动构建、安装驱动或启动容器 |
| [官方镜像 repository](https://quay.io/repository/ascend/vllm-ascend) | 官方来源；按明确 digest 固定内容，记录目标 Docker 的实际 RepoDigests |
| [Docker pull](https://docs.docker.com/reference/cli/docker/image/pull/)与[inspect](https://docs.docker.com/reference/cli/docker/image/inspect/) | 区分 tag、repository digest 和内容 image ID；拉取可复用已有层；缺身份不能仅信任名称 |
| [Docker load](https://docs.docker.com/reference/cli/docker/image/load/) | load 同时恢复镜像和 tag，因此必须先查冲突；API 1.48+ 才支持 load 的 platform 过滤，本实现通过单镜像归档预检支持较早 daemon |
| [Moby 镜像加载源码](https://github.com/moby/moby/blob/v26.1.3/image/tarexport/load.go)与[归档导出源码](https://github.com/moby/moby/blob/v26.1.3/image/tarexport/save.go) | Docker save 的 manifest、config 与层布局依据；不执行归档内代码，也不在主机目录 extractall |

## 示例配方与资料差异

0.18.0 安装页列出 `v0.18.0` 为 Atlas A2、`v0.18.0-a3` 为 Atlas A3、`v0.18.0-310p` 为 Atlas 300I，各有 openEuler 变体。assets 中的 0.18.0 请求只用于说明字段和开发核验，执行前重新核对实际硬件、版本、身份和官方资料。其他版本使用该版本官方资料重新生成 recipe。

本次安装页要求 CANN 9.0.0、torch-npu 2.9.0.post2，而同版矩阵仍记录 CANN 8.5.1 与另一 torch_npu 构建。两页不一致，不能自动选一个值宣称兼容；保留两份依据，结合所选镜像发布/构建输入和目标驱动进一步复核。脚本因此保持 hardware_compatibility 待复核、runtime_support 未评估，资产身份通过不能替代这些检查。

## 来源身份

官方 registry 镜像优先按 repository digest 交接。镜像 ID 是 config 内容身份，rootfs.diff_ids 固定未压缩层内容；repository digest 是 registry manifest 身份，二者不能混用。tar 文件 SHA256 则固定运输归档字节。离线 tar 或派生镜像若缺少上游 RepoDigests，提供原始发布记录或构建证据并保留来源待复核；不要伪造上游 digest。
