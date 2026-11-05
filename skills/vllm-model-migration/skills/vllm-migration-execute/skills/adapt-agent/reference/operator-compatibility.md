# 平台兼容检查

以目标实际 CANN、torch-npu、vLLM-Ascend 和硬件身份为准，版本字符串和参考 PR 不能代替内容及运行证明。Torch 名称不意味着所有 dtype/layout/shape 在 NPU 可用，Triton 名称也不意味着 CUDA 与 Ascend 实现等价。

对触及路径记录算子、输入 shape/dtype、layout、cache 所有权、prefill/decode、parallel group 和 kernel 可用边界。先核对已有实现和能力，再做可控 reference 数学测试及真实设备检查；错误配置明确拒绝。平台通用能力按维度/布局/功能判断，模型特有结构留在模型包。

仅允许必要兼容改动和既有 NPU 算子。缺少可用实现或必须新增算子时，保留阻塞原因、证据及建议，不自动研发、升级、降功能、发 issue 或发布。graph、量化、MTP 等只有明确请求并有对应验收才纳入支持结论。
