# 目标版本注册机制与资料

调研日期：2026-10-14，Asia/Shanghai。基线为 vLLM/vLLM-Ascend 0.18.0；实际开发 SDK 的安装身份必须另行读取，版本字符串不能代替内容 SHA256 或 image digest。

- [vLLM 0.18.0 模型注册](https://docs.vllm.ai/en/v0.18.0/contributing/model/registration/)：内置映射由 registry.py 的 _VLLM_MODELS 建立；外部实现可以通过 ModelRegistry.register_model 使用 module:class 字符串 lazy 注册。
- [vLLM 0.18.0 Plugin System](https://docs.vllm.ai/en/v0.18.0/design/plugin_system/)：general plugin 使用标准 entry_points、VLLM_PLUGINS 名称过滤，并需在参与运行的不同进程中加载；callback 应可重入。
- [固定 registry 源码](https://github.com/vllm-project/vllm/blob/bcf2be96120005e9aea171927f85055a6a5c0cf6/vllm/model_executor/models/registry.py)：该版本的 lazy entry 保存 module_name/class_name，load_model_cls import 模型；register_model 本身可以覆盖旧映射，因此本 Skill 必须预先拒绝冲突。
- [固定 plugin loader](https://github.com/vllm-project/vllm/blob/bcf2be96120005e9aea171927f85055a6a5c0cf6/vllm/plugins/__init__.py)：general plugin 由 load_general_plugins 调用，不能仅查看 entry point 文件而宣告已加载。

脚本只支持已核对的注册机制；目标版本改变构造形式、lazy 字段或 API 时阻塞并核对新版本，不自动 fallback，不改架构名，也不将其他环境的开发初检升级成部署验收。
