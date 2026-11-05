# 量化边界

解析原始配置中的 dtype 和量化信息，分别核对模型 loader、parallel/shard 规则及目标平台能力。未知量化格式、scale/zero-point 配对不完整或目标版本无实现时阻塞，不通过更改配置声明“兼容”。

当前通用脚本检查明确 dtype 的 safetensors key/shape 和直接映射/拼接，不实现量化转换、反量化或复杂重排。量化适配需独立扩展契约、定向数学/loader 检查及真实运行验收，不能用 BF16 个案关闭该缺口。
