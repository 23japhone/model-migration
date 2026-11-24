# 预检请求与交接契约

Python 3.10+，运行脚本只依赖标准库。物理目录与其他任务同级；安装或搬迁时须同时保留 vllm-model-migration/references/task-result.schema.json。该目录当前仅为共同静态资源，不调用它的执行入口。普通说明用中文，专有名词和机器标识保留原名。

## 请求

| 字段 | 规则 |
|---|---|
| model | path、task、modalities、dtype、quantization 均必需。task 为 chat/generate/embedding/rerank；模态为 text/image/audio/video；dtype 明确为 bfloat16/float16/float32；无量化使用 null，不推断 GPU 参数 |
| target | vllm、vllm_ascend、platform、hardware{name,family} 均必需；可携带准备交接中的 backend=vllm-ascend，不支持其他后端 |
| sources | vllm、vllm_ascend 各有本地 path。默认 kind=git，完整40位 revision，可选 expected_identity；kind=installed_package 则必须有 distribution/version/image_id/expected_identity，不接受 revision。安装包身份记录 not_applicable，不伪造 Git commit |
| reviews | version_pair 和 platform_support 记录列表；未知、失败和多个坏记录分别保留，不因某条损坏抹去其他有效 fail |
| features | 非空、无重复的显式功能标识列表，默认 ["eager"]；所需功能必须全部被平台资料覆盖，脚本不启用这些功能 |
| implementation | 可选 architecture、path、class_name，用于缺少内置映射时定位已有实现；只核对文件 AST 中的 class，不证明 import、forward 或权重加载兼容 |
| adaptation | 可选 allowed_scope（model/processor/platform）和 reference{path,class_name,sha256}。参考 class 必须对应待适配架构，摘要必须匹配；这是允许范围和参考资料，不是自动改动授权或实现产物 |
| prepare_handoff | 可选已有 prepare-handoff.json。核对阶段和模型回执 schema/task/run_id/终态及模型回执摘要、target/path/task/modalities 和当前 config SHA256；仅核对当前 metadata，不重新核对全部权重 |

顶层、model、target、sources、implementation、adaptation 的未知字段拒绝。请求不提供 shell、任意命令、SSH 地址、认证或安装参数。模型相对路径基于 work-root；其他输入相对路径基于请求文件所在目录。源码 revision 是调用方声明；脚本保存本地内容身份，revision_verification=not_evaluated，不假装自动验证 Git 或上游归档身份。

## 资料记录

每条记录有 topic、target、url、checked_at（YYYY-MM-DD）、evidence_file、evidence_sha256、conclusion、note。conclusion 为 pass/fail/review_required；note 是实际阅读后的中文说明。只接受 vLLM/vLLM-Ascend/昇腾官方 HTTPS 来源，无凭据、查询参数或片段。

platform_support 还须声明 architectures、tasks、modalities、dtypes、quantization、features；每项为非空列表，无量化项为 null。只在同一架构和全部本次能力均被覆盖时采用该记录。未知功能不因通用 pass 获得支持。任何匹配且有效的 fail 优先于 pass。脚本核对结构、文件摘要和目标一致性，**不自动理解网页、验证作者判断或证明实际环境支持**。

资料不得跨硬件、版本或量化条件复用。平台明确 fail 时，仅在具体参考和 platform 允许范围齐备时可以给出 adaptation_required；未知平台资料仍为 blocked，不把未知改写成可适配。

## 静态核对范围

保留原始 config 和嵌套 *_config，details.model.declared_stacks 逐栈保留明确声明的层数、类型序列及数量冲突，缺少类型时留待 worker 核对。根 architectures 是候选入口；不把 text_config 中骨干架构当作完整多模态模型。量化检查包括嵌套配置，无法识别时阻塞。chat_template 接受准备任务已经支持的 tokenizer/processor/chat_template JSON、Jinja 文件或 additional_chat_templates；多模态请求要求 processor/preprocessor 配置。资料存在不证明模板渲染或 processor 可运行。

内置注册解析支持常量 dict、命名 dict 合并、dict 展开、| 合并，以及遍历常量 dict.items() 的单层 dict 推导式（常量元组解构、单项相等/不等条件）。未知动态表达式、动态修改或无法定位 class 时保留具体原因。尚未运行 out-of-tree plugin 发现和 import，所以 missing 内置映射不能表述为“运行环境没有任何注册”。实际安装/运行映射由后续注册检查负责。

注册表的 dotted module 按 models 下的嵌套模块文件或包 __init__.py 定位；须唯一对应并直接声明 class。非法标识、模块与包同名歧义、路径越界和符号链接不能成为静态候选，不导入模块来猜测实际解析结果。

完整性检查同时覆盖根表、来源子表和承载原表引用的 alias/容器。条件、循环或嵌套作用域中的表绑定，传给未知函数的原表引用，以及 import 替换或返回原表引用，均为未知效果并阻塞；不执行代码来猜测分支。模块级常量重赋值、仅有类型标注、已支持的常量推导式及 dict.items/keys/values/get/copy 读取仍可分析。copy 后的独立 dict 不等于原表；该检查只证明本入口能解析的源码形态，不承诺发现任意间接运行注册。

源码身份格式 precheck-python-file-list-v1：按相对 POSIX 路径排序，列出 Python 文件的 path/size/sha256，以 UTF-8、ensure_ascii=False、字段排序、无多余空格序列化后计算 SHA256。跳过 .git、__pycache__、.venv；限制 10,000 个文件和每文件 4 MiB，拒绝源码越界链接。此身份不包含非 Python 构建输入、不证明完整仓库、revision 或派生镜像；实际运行载体仍需独立核验。

## 输出与状态

每次在 `<work-root>/<run-id>/vllm-model-precheck/<attempt-id>/` 创建独立尝试：

- request.json：规范化、脱敏的冻结请求。
- precheck-manifest.json：共同 1.0 外层，details 含原始配置、源码内容身份、注册候选、资料、支持矩阵、route、selected_architecture 和 reasons。
- model/sources/registry/reviews/optional-snapshot.json：各自的事实与原因；资料文本为脱敏快照，evidence_sha256 标识原资料而非脱敏副本。

details 是共同 Schema 已允许的扩展字段，不修改准备任务外层或现有 Schema。输入、配置、Python 源码和原资料 SHA256 不等于运行验收。所需配置/源码/资料缺失时，保留独立可读分支，status=blocked/退出 3；另外三条路线 status=completed/退出 0。support=pass 仅是静态候选，其他路线为 review_required，runtime_support 恒为 not_evaluated。

退出 2 表示顶层输入/输出保护/共同资源无效，未创建运行目录；退出 1 表示取消或证据未完整保存，不能宣告完成；退出 0 的 --plan 仅表示输入有效、status=not_run。输出保护覆盖模型、两套源码和实际技能安装根。单文件原子替换不构成多文件事务；不承诺自动恢复、并发锁或 exactly-once。
