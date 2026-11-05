# 版本与硬件资料

访问日期：2026-10-14。版本示例仅用于开发核验，调用方应根据实际目标选择固定版本资料。普通说明用中文，vLLM、vLLM-Ascend、CANN、torch-npu、platform 等名称保留原名。

| 资料 | 复核用途 |
| --- | --- |
| [vLLM-Ascend 固定版本源码](https://github.com/vllm-project/vllm-ascend/tree/v0.18.0) | 对照版本配套、镜像构建和依赖声明；实际开发记录需保存 commit 或文件摘要 |
| [vLLM-Ascend 0.18.0 安装资料](https://docs.vllm.ai/projects/ascend/en/v0.18.0/installation.html) | 查看 Linux、Python、昇腾硬件与 CANN/torch-npu 等条件，不执行其中服务启动命令 |
| [vLLM-Ascend 0.18.0 版本策略](https://docs.vllm.ai/projects/ascend/en/v0.18.0/community/versioning_policy.html) | 复核所选 vLLM 与 vLLM-Ascend 是否配套，不能仅比较版本号相同 |
| [昇腾官方开发资料](https://www.hiascend.com/document) | 根据实际设备查找驱动、固件与 CANN 的适用条件并保存具体版本页面 |

本次开发另取固定 commit [e18643f8 的版本矩阵](https://github.com/vllm-project/vllm-ascend/blob/e18643f8a4d5bd9990727654318ad069ea0b56e2/docs/source/community/versioning_policy.md)、[安装资料](https://github.com/vllm-project/vllm-ascend/blob/e18643f8a4d5bd9990727654318ad069ea0b56e2/docs/source/installation.md)及 [Dockerfile](https://github.com/vllm-project/vllm-ascend/blob/e18643f8a4d5bd9990727654318ad069ea0b56e2/Dockerfile)核对既有 0.18.0 镜像。矩阵与构建资料列出 CANN 8.5.1，而本次访问的在线安装页面列出 9.0.0；因此不能混用它们判断同一个镜像的软件栈。固定源码的文档替换变量也可能滞后，应交叉查看对应版本矩阵、构建输入和实际发布资料，不仅依赖页面或变量名称。

在线页面可能更新，URL 含版本并不意味着页面内容永远不变。保留实际阅读的资料快照、SHA256、日期及复核说明；若页面与固定源码或目标要求不一致，列出差异，结论保持 review_required 或 fail，不自动升级环境。模型支持度、量化、模态和真实驱动/CANN 安装仍由后续预检与运行验证分别确认。

模型下载、tokenizer/processor 与来源规则继续使用 [model 来源](../skills/vllm-model-prepare/references/sources.md)，镜像具体规则使用 [image 来源](../skills/vllm-image-prepare/references/sources.md)，原始配方和 split 使用 [dataset 来源](../skills/vllm-dataset-prepare/references/sources.md)。阶段不会创建第二套下载配方或评测规则。

网络异常不能直接归因于代理。由失败来源所属任务复核 DNS、TLS、权限、目标可达性和实际执行环境；确认需要代理后提示用户配置并等待。HTTP 下载代理配置属于执行进程；registry 下载实际由 Docker daemon 执行，需用户按该 daemon 的网络条件配置，不由阶段修改 daemon。留存失败证据并继续不依赖该来源的任务。
