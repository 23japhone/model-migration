# 实现与交接检查单

按模型和请求选择检查内容，保留每项结论及未覆盖原因。

- 原始架构、嵌套文本/视觉配置、任务与模态、dtype/量化及 processor 固定；预检缺口与适配目标一致。
- vLLM 初始化、forward、IntermediateTensors、attention metadata、parallel layers、logits 等 API 对齐目标版本；参考实现与实际回移代码分别记录。
- 源 checkpoint key→实际目的 parameter/buffer、shape/dtype、融合顺序、shard loader、tied weights、persistent scalar/buffer、共享 KV producer/consumer 的完整行为均有依据。未知/重复/丢失权重应失败；未启用模态逐 key 说明，不能吞掉文本/图像遗漏。
- 从 config 推导期望表，实际 loader 和 reference 对照另测；静态表通过不能代替加载 tensor。
- 视觉 patch/位置编码、normalization、pooling、图像占位 token 数、processor 与 tokenizer/template 的原始语义保持一致。
- 平台能力使用已有算子；覆盖触及的 head 维度、布局、cache、prefill/decode、SWA、共享 KV 和 RoPE 数学。配置不支持时明确拒绝，不静默 fallback。
- 按现行结构规范审计全部适配 diff 和直接同级目录；硬热点各指标不增长，公开导出和原行为回归齐全。
- 实际运行定向套件，按当前完整源码身份绑定日志/命令；每次修改后重跑受影响检查，旧成功不覆盖新失败。
- check 后审阅完整 diff、映射及排除理由；注册映射交给注册入口。运行载体逐文件匹配、注册复验、真实权重和功能交给验证入口。
- 交付真实验收、离线测试、未验证项，说明资源最后状态及目录职责；等待用户分项验收。

权威来源：[vLLM 0.18.0 模型实现](https://docs.vllm.ai/en/v0.18.0/contributing/model/basic/)、[注册](https://docs.vllm.ai/en/v0.18.0/contributing/model/registration/)。[Ascend RFC #7539](https://github.com/vllm-project/vllm-ascend/issues/7539) 仅作流程参考；其中提交、发布或自动恢复步骤不构成本 Skill 的授权。
