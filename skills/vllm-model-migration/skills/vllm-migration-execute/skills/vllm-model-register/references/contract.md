# 注册请求、结果与复验契约

Python 3.10+。脚本本体只用标准库；实际注册检查要求用户指定的目标解释器已有对应 vLLM 及模型 import 依赖。plugin 安装只用该环境已有 pip，不联网、不安装依赖、不改系统解释器或 SDK。

## 公开入口

register.py 和 check.py 均使用 --request、--work-root、--run-id、--plan。计划模式只规范化请求、检查共同静态 Schema 与路径保护，不读取目标文件或执行目标代码，不创建运行目录。执行模式的相对输入路径基于请求文件目录；不把维护 checkout 或固定 cwd 当作安装根。

| 字段 | 约束 |
|---|---|
| mode | source 或 plugin |
| allowed_changes | 显式列表，允许 registry/plugin；空列表只能复用已通过的映射或已加载 plugin |
| model | config 为原始 config.json 文件，architecture 必须仍存在于原始根 architectures；不改配置或名称 |
| implementation | module 为完整 Python module，class_name、path、sha256 全部必需；文件直接声明已有 class，不执行任意转换 |
| target | vllm、registry_path、registry_sha256 必需；可指定 vllm_ascend。registry_path 为标准 vllm/model_executor/models/registry.py，SHA256 绑定明确的注册前源码；版本的 +build 后缀保留于实际报告 |
| runtime | python、import_roots、plugins、timeout_seconds、context 全部必需。python 保留虚拟环境入口，不先解析链接切换解释器。import_roots 明确代码位置；plugins 是允许加载的 entry point 名称（包括需要的平台 plugin），空列表禁用；timeout 为 0.1—300 秒；context 为 development/deployment |
| plugin | plugin 模式必需 project_root、install_site、name、version、entrypoint。名称为 Python 标识符，版本为三段数字；两个输出根必须隔离、不重叠，install_site 明确加入 import_roots |
| precheck | 可选 manifest、sha256；核对 1.0 外层、task、completed、支持路线、所选 architecture、版本资料及 config 身份。不提供时标明独立调用；不冒充已有交接 |

未知字段拒绝。源码注册使用 models 下已有模块，允许 dotted module 对应嵌套 module.py，或包的 __init__.py 直接声明 class；模块与包同名歧义、路径不符和目录越界均拒绝。外部实现使用 plugin。请求不接受 shell、任意命令、凭据、服务管理或安装 SDK 参数。输出不得位于模型、目标 SDK 源码或技能安装资源中；plugin 不写入解释器全局前缀。新增目录只由本任务管理，既有无归属目录、不同产物和注册锁均拒绝覆盖或清理。

## 注册与初检

先独立采集 config、实现、registry 与可选预检资料，再由独立目标进程读取真实 ModelRegistry 与允许 plugin，并核对实际版本、registry 路径/SHA256 和 class import 路径/SHA256。冲突不修改源码。不存在源码映射且允许 registry 时，在唯一 ModelRegistry 构造入口前插入原始 architecture 的常量映射扩展；原文身份在原子替换前再次核对，仅持有本次创建的短锁，保留权限。

plugin 产物包含 lazy callback、标准 .whl、entry_points.txt 和 RECORD；利用 vllm.general_plugins 加载 register()，不直接调用 callback 冒充框架加载。callback 可重入、对相同映射复用、对不同映射报错。安装使用现有 pip 的 --isolated/--no-index/--no-deps/--no-compile/--target，只触达显式隔离 site。实际检查同时核对 distribution、entry point 和对应源文件，再在 loader 调用前核对 Python 实际解析的 SourceFileLoader/path/SHA256；仅 import 已核对的生成模块，不手动执行 register()。load_general_plugins 后再次核对实际 module、register 函数路径、摘要和内存加载状态。distribution 的文件路径不能冒充实际 import 身份。缺少 entry point 且同名 module 已存在、同名遮蔽、预加载 module 身份不符、未知 loader 或加载后身份变化均阻塞；未加载时不能自动放宽 VLLM_PLUGINS。

最终再次启动新的检查进程，验证真实映射并通过该条目的 load_model_cls() import 指定 class，不实例化。子进程请求有 nonce/phase/PID/context 绑定，日志脱敏；SDK 缓存重定向到本尝试目录并禁用 Hugging Face 在线访问。授权目标代码的其他 import 副作用仍需调用前审查，不承诺任意第三方代码的所有行为都只读。

自有短命令创建独立进程组，正常结束也清理组内残留。SIGTERM 后最多等待 3 秒，再对仍存在的自有组发送 SIGKILL，输出管道最多再等待 3 秒；主进程提前退出或派生进程关闭输出管道均不能跳过组清理。PID 被复用、组身份不符或无法收回管道时返回明确错误/cleanup_failed，不清理未知组或脱离原组的第三方进程，不报告成功。被系统接管等待回收的 zombie 与仍可执行的派生进程分别记录；本 Skill 不增加跨会话进程调度或服务生命周期能力。

## 证据与状态

在 `<work-root>/<run-id>/vllm-model-register/<attempt-id>/` 创建独立尝试：

- request.json、inputs-snapshot.json 和 registration-manifest.json：共同 1.0 外层，保留输入、步骤、注册效果、检查、错误及未覆盖项。
- source 模式：registration.diff 仅包含新增映射行；registry-before/after.json 保存路径、大小及摘要，避免复制无关原始源码或凭据。
- plugin 模式：plugin-artifact.json、wheel 身份、明确的项目与安装 site；部分失败保留 installing 标记，不能将半成品当作复用。
- before/after-probe-input/output.json、process.json、stdout/stderr.log：独立进程身份和实际检查证据。
- registration-check.request.json：注册效果已执行后交付的检查请求；source 方式更新 registry_sha256，部署时按实际载体路径重建等价请求，保留代码内容约束。

退出 0/completed 仅用于必需检查全部通过，conclusions.registration=pass；weight_loading/functional_validation 恒为 not_evaluated。退出 3/blocked 用于资料、映射、依赖、版本或初检不足。退出 2 是请求/静态资源/保护无效；退出 1 是超时、取消或执行/证据未完整保存，不能宣告完成。证据可写时，执行失败或取消保存 failed 终态、错误与已执行效果；证据写入本身失败时输出明确错误，旧 running 清单只能作为未完整保存的尝试。running、blocked、failed 不能用于成功交接；有 diff 或已安装产物但复验失败仍不完成，不自动回滚、重试或清理其他任务。

check.py 不执行注册修改或安装，但会在自己的独立进程中按声明的 plugin 策略建立内存映射并 import class。它消费运行载体的实际路径及相同内容身份，不硬编码开发路径、不负责把 diff 或 wheel 放入镜像，不证明最终载体的权重/NPU/服务能力。
