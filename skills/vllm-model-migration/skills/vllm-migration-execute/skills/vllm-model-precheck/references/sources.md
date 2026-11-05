# 固定版本资料核对方法

不要根据模型名称或 latest 支持表判断目标版本。先取得模型的原始 config、tokenizer/processor 元数据和对应 revision，再按目标源码定位注册、模型实现和框架/平台依赖。

| 资料 | 用途与限制 |
|---|---|
| [vLLM 0.18.0 Basic Model](https://docs.vllm.ai/en/v0.18.0/contributing/model/basic/) | 模型类、并行层和 load_weights 说明；模型实现完成不等于注册或推理通过 |
| [vLLM 0.18.0 注册](https://docs.vllm.ai/en/v0.18.0/contributing/model/registration/) | 内置和 out-of-tree 机制；本任务 AST 检查只覆盖内置常量表和指定实现 |
| [Plugin System](https://docs.vllm.ai/en/v0.18.0/design/plugin_system/) | general plugin 在运行进程中的加载；最终实际映射需要运行环境只读检查 |
| [固定 vLLM 注册源码](https://github.com/vllm-project/vllm/blob/bcf2be96120005e9aea171927f85055a6a5c0cf6/vllm/model_executor/models/registry.py) | 0.18.0 基点示例；其他目标使用实际 revision，不把它设为通用默认 |
| [固定 Ascend 版本策略](https://github.com/vllm-project/vllm-ascend/blob/e18643f8a4d5bd9990727654318ad069ea0b56e2/docs/source/community/versioning_policy.md) | 核对对应版本、硬件和软件栈，保留材料身份 |
| [固定 Ascend 模型矩阵](https://github.com/vllm-project/vllm-ascend/blob/e18643f8a4d5bd9990727654318ad069ea0b56e2/docs/source/user_guide/support_matrix/supported_models.md) | 区分支持、实验性支持和未验证；逐一核对模态、dtype、量化、并行和功能 |
| [固定 Ascend Dockerfile](https://github.com/vllm-project/vllm-ascend/blob/e18643f8a4d5bd9990727654318ad069ea0b56e2/Dockerfile) | 源码基点的构建输入；不证明已准备的镜像内部内容一致 |

网页版本路径仍可能与源码材料不同。保留 URL、读取日期和原文 SHA256，说明复核结论对应哪份源码/镜像。版本策略、Dockerfile 和在线安装指南有冲突时分别记录，不自动升级依赖或套用另一套参数。

需要适配的模型应附固定摘要的参考实现，并明确允许改动范围。开放 issue、Draft RFC、未合并 PR 和其他硬件的结果仅是参考，不能用其标题或截图填写当前部署通过。若资料无法覆盖所需能力，保留 review_required；后续实测不能由本任务猜测。
