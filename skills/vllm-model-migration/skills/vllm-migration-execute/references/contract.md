# 阶段请求与共同交接

阶段使用 `--request --work-root --run-id --action run|finalize --plan`，默认 run。输入 UTF-8 JSON 对象，拒绝未知字段、重复键和非法引用。`{path,sha256}` 固定原始资料字节；全部路径使用规范绝对路径。运行目录和完整安装包、模型、SDK、构建输入隔离；证据不能经符号链接重定向。

## run 请求

| 字段 | 契约 |
|---|---|
| schema_version | 1.0 |
| preparation | prepare-manifest.json 的 path/sha256；1.0、vllm-migration-prepare、completed，preparation/assets/version_materials 全pass；model/image必需任务completed/assets=pass |
| precheck | 预检请求 JSON 的 path/sha256。模型位置、task/modalities、版本须匹配准备；由阶段通过公开CLI重新预检 |
| verify | 验证请求 JSON 的 path/sha256。完整模型清单须覆盖准备的文件且内容相同。服务、实际镜像、运行源码、注册检查及用例须显式固定；目标载体路径允许不同 |
| register | 可选注册请求JSON的path/sha256，需注册/适配路线必需。target版本、原始architecture/config及实现内容关联；注册方式须与载体一致，plugin的name/version/entrypoint也须一致；allowed_changes仍由注册请求显式授权 |
| register_container | 可选已有Docker执行位置：container_id（完整64hex）、image_id（sha256:64hex）、started_at、python（容器中已安装解释器绝对路径）。核对前后CID/image/StartedAt，复制注册完整资源并执行公开CLI，保存原始容器路径与宿主证据映射。不会启动/停止容器或安装目标SDK |
| adaptation | 可选completed/check适配回执path/sha256，adaptation_required路线必需；预检来源、架构、版本、原始config必须等价。允许新预检运行时间/报告SHA不同，但实际sources事实必须相同 |
| adaptation_roots | 适配来源名→运行载体中SDK根。每个改动必须在verify.runtime.files的对应完整路径及SHA256出现；源码注册可在适配后的registry上派生映射，注册前SHA必须等于该适配文件，注册后SHA必须等于载体的注册检查请求；其他文件不允许只按摘要集合替代路径。当前正向运行文件契约无法证明删除项，删除改动阻塞，不能默认为已移除 |
| timeout_seconds | 单次实际子CLI最长等待，默认14400，1—86400秒；计划最多60秒。目标任务另保留自身更细超时 |

阶段仅通过固定的precheck.py/register.py/verify.py公开CLI通信。执行前调用其plan得到规范化输入，再冻结到包外；完整输入/run_id/任务标识/退出码/回执位置均核对。原始任务请求不因展示脱敏而修改；调用者必须保证请求、路径、argv与原始日志不含凭据。阶段结果及诊断对常见凭据展示脱敏，不保证识别所有自由文本秘密。

支持路线跳过adapt/register但不跳过载体注册。适配路线先完成公开适配步骤并交接，脚本不改模型源码；缺少交接时next_action=adaptation_required。必要注册缺失时next_action=registration_required。必需前置失败只阻塞依赖，独立输入资料仍逐项采集错误。

准备镜像是已核对的输入来源；实际镜像可以为明确的派生载体，阶段同时记录prepared/requested/build，由verify重新核对实际安装版本、全部源码及构建输入。两者不同不能解释为准备镜像已经具备全部适配。阶段不自动选择派生镜像或修改配方。

## finalize 请求

`{source:{path,sha256},review:{path,sha256}}`。source必须是blocked、next_action=semantic_review_required的本次阶段；review必须是针对其唯一raw verify回执、operation=review、success=true且七类必需结论全pass的独立结果，部署交接路径必须一致。

先重新只读核对原阶段准备、路线、适配及注册关联，拒绝非语义检查失败或过期身份；再调用verify公开review，对同一source、全部必需响应/原始HTTP/SSE/cases清单和部署交接摘要再次只读核验；原始证据变更则拒绝。新阶段引用原始任务和本次复验报告，不重放预检/注册/构建/服务启动；该操作不刷新设备或服务状态。

## 结果、失败及资源

`work-root/run-id/vllm-migration-execute/UUID` 包含 migration-manifest.json、task-ledger.json、migration-handoff.json、冻结请求、计划/执行stdout/stderr与process记录，以及子任务/容器复制的证据。共同外层保持1.0；conclusions包含preparation、migration、functional_validation。必需verify七类结论全部pass且success=true、部署交接完整后才migration=pass。

指定 register_container 时，静态资源按总入口规则从当前目录枚举，无需构建清单；复制前后核对源和目标集合及字节摘要。本次成功复制的实际身份另存为 `registration-resource-identity.json`，格式仍为相对总 Skill 根的路径到 SHA256 映射，不改变请求或阶段结果外层，也不改写历史记录。资源变化或身份写入失败时不继续本次注册，不自动重试。

复制前的原始注册请求单独保存为本次 attempt 下的 `register.request.json`，不写入 `registration-resources/` 静态资源副本。迁移结果续接和三 JSON 导出均核对该请求与原声明一致，再核对 `register-origin.json` 的容器及远端→本地回执映射、`register-plan.stdout.log` 的规范化输入与注册结果。文件缺失或身份不一致则拒绝对应续接/导出，不回退到静态资源目录中的同名请求，不改写历史回执。

退出0=阶段完成，2=顶层输入/计划/安装包输出保护无效，3=路线/输入资料/交接不足或语义待复核，1=取消、超时、执行或证据失败。未完成保存原因及明确next_action；证据不可写时不能保证终态文件，但退出失败、不宣告completed。单JSON原子替换不代表多文件事务。

终态发布先固定task-ledger，再生成migration-handoff对该台账的SHA256，最后写migration-manifest；交接发布后不再次改写台账。正常、阻塞、失败、取消及finalize都使用同一发布顺序。外部计划/执行回执必须为JSON对象；Docker inspect须为单项对象列表，查询也记录自有CLI输出、超时/取消和process状态，并收敛阶段失败。

finalize新尝试建立后，复核CLI失败或取消会写failed终态及resolve_review_failure；原始阶段不变。基础证据无法落盘时仍明确失败，不保证能够写入终态。

取消/超时仅通知本次自有CLI，等待其执行自身清理；30秒未收敛才回收自有CLI组，资源清理未证实须明确记录并失败，不直接向NPU worker发信号、不使用全局pkill。容器的外部效果可能已经发生，必须读取原任务交接并复核后再决定是否重跑；阶段不自动回滚、重试或停止其他任务。

静态/受控检查不能替代真实模型验收；来源与回执的SHA256不是签名或可信执行证明。完整资源安装/发现/触发的实际调用环境需独立验收。

本阶段当前实现chat/generate、文本/图像、eager、非量化的功能门槛。所请求task/modalities必须出现在verify.required_features；其他模态、graph/其他features或量化请求显式阻塞，不自动降级。阶段保存子任务原有uncovered时，其说明属于该子任务边界；整体是否完成以阶段结论和真实verify/review证据为准。
