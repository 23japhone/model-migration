# 适配请求与交接契约

CLI：`--request --work-root --run-id --action capture|check --plan`，默认 capture。UTF-8 JSON 根为对象，重复字段、未知顶层/输入对象字段拒绝。共同结果 schema_version=1.0，skill_version=0.2.0-dev.1。Python 3.10+，自身标准库。

全部 JSON 资料及 safetensors header 限制嵌套不超过 128 层；超过限制或解析递归超限返回明确资料错误。各独立来源、权重及定向检查保留本分支诊断并继续有前提的其他分支。非法请求在写运行目录前拒绝。

输入模板：[capture](../assets/capture.request.example.json)、[check](../assets/check.request.example.json)。模板中的路径、revision 和全零摘要均须替换为真实身份；模板只说明形状，不证明资产可用。

## capture

必需字段：schema_version、model、target、precheck、sources、references、required_checks。

- model：root（绝对路径）、files（全部必需资产相对路径→SHA256）、weight_files（全部 safetensors 文件）、config（原始配置相对路径）、architecture（原始 architectures 成员）、modalities（明确模态列表）。真实读取全部列明资产并计算 SHA256，发现清单外权重、丢失 shard 或未固定 index 均失败。保留嵌套原始配置。
- target：vllm、vllm_ascend 准确目标版本。
- precheck：`{path,sha256}`，消费 completed、1.0、task_id=vllm-model-precheck 回执；version_materials=pass、route=adaptation_required，selected_architecture 必须与请求 architecture 相同，support_matrix 中必须恰有一个该候选且 route=adaptation_required；config、请求模态、目标版本、各来源 revision 及完整 Python 文件摘要清单必须一致。support=review_required 是待适配的正常输入，不代表 NPU 通过。
- sources：名称→`{baseline,working,revision,origin,allow}`。baseline、working 是不同且不嵌套的完整源码副本；不同来源及模型根之间亦不得嵌套。origin 为 `{path,sha256}`，其 JSON 含 revision、tree_sha256、acquisition。acquisition 记录可信归档、实际获取命令/来源及校验依据，执行者负责核对；脚本验证完整树与记录一致，不自行下载或证明官方 revision。
- allow：非空规范相对文件或目录路径列表。目录边界为 path 或 path/子项，不使用 glob；新增、删除、内容及文件 mode 差异均检查。只排除 `.git`、`__pycache__`、`.pytest_cache` 工具元数据，不排除业务源码。其他文件、权限和 symlink 变化必须可见；符号链接拒绝。任一非排除目录无法枚举或文件无法读取，均阻塞完整树检查，不能静默跳过。
- references：非空 `{path,sha256}` 数组，固定上游实现、资料及许可审查输入；只计算内容身份，不执行其中代码。
- required_checks：本次全部必需定向检查 id 的唯一非空数组；按真实模型/模态决定，不能为通过而删除失败项。

完整树 fingerprint 算法：每个普通文件的规范相对路径→`{sha256,mode}`（mode 为权限整数），按 key 排序、紧凑 JSON、UTF-8 编码后 SHA256。Python 可用 `hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()`。来源根必须规范绝对路径，不接受 symlink。

## check

必需 schema_version、context（capture 回执 path/sha256）、weight_mapping（映射 JSON path/sha256）、checks（定向记录 path/sha256 数组）。核验时先读取 capture，重新保护所有来源根再写证据。再次核对原始资产、来源和参考资料，任何冻结输入变化都阻塞；工作源码允许在明确范围内变化。

映射 JSON：schema_version、config_sha256、source_fingerprints（来源名→当前完整工作树 fingerprint）、destinations（目的名→shape/dtype）、mappings、ignored（未启用 source key→明确理由）。mappings 每项为 `{source,destination,shape,axis,offset}`。shape 为原 checkpoint shape；axis=null、offset=0 表示同 shape 直接映射；整数 axis/offset 表示沿该维拼接至完整目的 shape。源 key 恰好映射一次或显式排除，不能未知、遗漏、重复；目的区间不得重叠、缺口或超界。完整 safetensors header/数据长度/index、dtype、所有 key 均核对。支持 BF16/F16/F32/F64、常见整数及 BOOL 元数据；复杂 reshape/转置/量化/分片规则不能伪装成拼接，通过定向 loader/reference 检查扩展契约前保留未覆盖。这里的目的表是执行者提供的配置派生契约，不自动证明实际模型参数或数学正确。

定向记录 JSON：id、status=pass/fail/review_required、source_fingerprints、config_sha256、command（真实 argv 数组）、artifacts（原始日志/报告 path/sha256 非空数组）、summary（中文说明）。所有必需 id 唯一齐全且 pass，所有原始资料存在且摘要一致，才能交接。脚本不执行 command；记录需由本轮实际检查产生，不能沿用过期来源、捏造日志或以声明代替运行。

## 结果与故障

结果含 task_id、run_id、inputs、input_identity、status、conclusions、evidence、uncovered、details。capture 仅 context=pass；check 通过才 adaptation=pass。registration、weight_loading、functional_validation 始终 not_evaluated。check details 保留完整来源树清单/fingerprint、实际差异、diff 路径/摘要、权重覆盖和定向记录，作为注册/验证的适配共同交接。后续 verify 请求须固定全部适配文件及独立注册变更，不能只依赖本回执。

退出：0=本操作 completed，2=输入形状/路径错误，3=核验阻塞，1=执行、取消或证据失败。最终证据失败不能报告 completed；旧回执不覆盖。独立来源或检查失败仍采集其他有前提的分支。SIGINT/SIGTERM 取消生成失败终态；终态写入被取消时只恢复一次发布，不能转成成功。无目标进程或服务由本 CLI 创建，因此没有服务清理副作用。

运行目录为 work-root/run-id/adapt-agent/UUID；不得与安装资源根、原始模型、源码基线或工作副本重叠。原始日志、源码、模型及凭据不入包；请求/命令不得含凭据，使用目标环境授权配置。远程执行复制完整资源，在已有授权连接中调用，不增加 SSH 或维护后端依赖。

## 证据隐私

原始资料只读、摘要按原始字节计算；输出 JSON 和 CLI 诊断统一脱敏常见凭据字段、Authorization 完整值、URL 的 userinfo/query/fragment 及 argv 凭据参数。冻结上下文比较使用同一安全展示，不把展示脱敏当作来源内容变化；原始 origin 的 path/SHA256 仍固定实际内容。保留 eos_token_id、tokenizer_class 等非凭据模型字段。

请求、路径、源码 diff 及原始日志仍须在生成时排除凭据；这些原始外部文件不由脚本改写，脱敏不保证识别任意自由文本中的秘密。检查输出不能代替凭据治理或资料可信性核对。
