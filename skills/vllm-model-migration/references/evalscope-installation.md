# EvalScope 共用环境安装契约

客户 task.json 的三组字段保持不变。安装设置只属于 Agent 内部 context；不要求客户提供额外文件。客户 accuracy.evaluator=evalscope（含大小写归一化；缺失/null/整数 -1 默认 EvalScope）使用本流程；先合成 prompt 的明确后端再判断，显式 AISBench 保留任务隔离环境流程。AISBench 与 evalscope_install 同时提供时报告不适用，不自动切换后端。客户未指定标记不扩展到下面的安装参数校验。

## 原版脚本与来源

[scripts/install_evalscope.py](../scripts/install_evalscope.py) 原样来自 `sglang-agent-profile` 仓库的 `vllm-ascend-performance-tuner/scripts/install_evalscope.py`，来源目录最近相关提交为 `0df047f2d6ac49bc7e04baa52a41c2cdc0ca14c4`，SHA256 为 `3b2aed60e2b73a7a8ae3a1db3f33a0219d9bf2159177303fa953ff9733ebadef`。副本未扩展；由回归检查字节摘要，纳入资源清单与完整 Skill 搬迁验证。

原脚本先解析基础 Python，创建或复用目标 venv，再执行 pip 安装 `evalscope[perf]`；包源固定为 `https://pypi.tuna.tsinghua.edu.cn/simple`。随后读取该 Python 的 scripts 目录、检查 EvalScope CLI 的 `perf --help` 所需选项、读取实际 EvalScope 版本。perf 帮助检查不证明精度 API、配置、数据集或评分兼容。

## 内部请求与参数

context 可选顶层对象 `evalscope_install`，字段如下。未提供对象等同空对象；下表默认值仅用于首次正式准备，续接逐字段继承原环境选择。未知字段、null、空字符串、错误类型拒绝；布尔值不能用 0/1 代替。

| 字段 | 类型 | argv | 默认值 |
| --- | --- | --- | --- |
| python | 非空字符串，解释器路径或命令名 | --python | 控制解释器 |
| venv | 非空路径字符串 | --venv | /data/evalscope-venv |
| version | 非空字符串 | --version | 不传，采用 pip 实际安装版本 |
| upgrade | 布尔 | true 时 --upgrade | false |
| dry_run | 布尔 | true 时 --dry-run | false |
| help | 布尔 | true 时 --help | false |

准备阶段公开入口 `skills/vllm-migration-prepare/scripts/customer.py` 接收 `{"operation":"evalscope_environment","install":{}}`。install 可省略。旧 python/candidates/selection 请求字段和 observations.evaluator_candidates 均拒绝。控制器用 argv 列表调用原脚本，不拼接 shell；venv 展开为实际绝对目录，但返回 Python 启动路径原样保留，不能 resolve 符号链接到基础 Python。

venv 不得与完整 Skill、task/context、模型、数据集或运行证据重叠。安装脚本独立调用本身仍遵循原版接口；客户编排和准备操作额外保证这些边界。版本参数原样透传；显式版本须与返回的实际版本字符串一致，使用实际发布版本号，不能用通配符取得就绪结论。

## 每次执行、结果与恢复

每次新任务或续接通过安装前核验、进入正式准备阶段，都调用安装脚本。既有 venv 也执行 pip；默认不传 --upgrade，只有本次显式 upgrade=true 才开启。移除自动发现、候选能力探测、条件安装、EvalScope latest 解析和旧 selection 复用流程；资产 SDK 与 AISBench 的隔离安装保留。

| 参数 | 首次正式准备 | 同一任务续接省略时 |
| --- | --- | --- |
| python | 显式值，否则控制解释器；仅解析该命令，记录绝对启动路径 | 继承创建 venv 的基础 Python；不能用安装结果中的评测 Python 代替 |
| venv | 显式值，否则 /data/evalscope-venv；规范化绝对目录 | 继承原目录 |
| version | 显式值，否则不指定 | 首次成功后传实际版本；此前沿用原版本要求（包括不指定） |
| upgrade、help、dry_run | 默认 false | 默认 false；不继承上次的 true |

省略整个对象、空对象、只传开关或部分环境字段均按上述规则补齐。首次正式安装前将基础 Python、venv、原版本要求及请求引用写入内部 `evalscope_install_selection`；即使安装失败或中间续接没有 context，也保留该选择。本次完整参数和开关保存在尝试请求中；首次成功以 `evalscope_environment` 绑定实际评测 Python 和版本。选择合成一次，先继承再检查目录边界，前置检查和执行共用同一结果。

正式安装已经开始后，显式改变基础 Python、venv 或版本要求须新 run-id。成功后只允许相同实际版本，upgrade=true 也同时传入这个精确版本；固定 EvalScope 不等于锁定所有依赖。本次选择冲突在安装前报告原值和本次值，不调用脚本、不使原绑定失效。

已有成功绑定时，先用绑定 Python 做 30 秒只读核验，检查启动路径、sys.prefix 与实际 EvalScope 版本，保存 stdout、stderr、argv、退出状态及超时信息。Python 启动路径原样保留，不将其符号链接解析成系统解释器。确认环境消失、venv 路径被重定向或身份变化时永久标记绑定 invalidated，要求新任务，不先重建或恢复原版本来掩盖变化。超时、权限不足、非零退出或异常输出只表示无法核验，保留绑定并阻塞环境，不自动重试。前检通过后仍调用原脚本并做安装后核验。

调用总超时 1800 秒，保存 `evalscope-install.stdout.log`、`evalscope-install.stderr.log`、`evalscope-install.process.json`（argv、退出码及超时/取消信息）；沿用现有敏感文本处理。stdout 允许前置 pip 日志，只解析末尾完整 JSON。退出码 0、status=success、实际版本有效且返回 venv/Python/CLI/package 与配置一致才准备完成。返回 python、venv、evalscope_executable、evalscope_version，并适配 `versions.evalscope`。精度请求与 catalog 使用同一 Python 和实际版本，不额外查询 latest。

help 优先于 dry_run；两者只返回帮助或安装预览，准备和客户回执均 status=not_run，不产生环境就绪结论，不启动模型、数据准备、部署、精度或交付。预览省略的环境参数使用继承值；允许明确预览其他目标，并提示正式执行须新任务。不运行绑定探测、不建立或修改选择/绑定，独立 `environment_preview` 回执不覆盖成功的 `environment` 回执。历史证据不足以恢复参数时阻塞预览，不能转入业务或猜默认环境。迁移 --plan 只检查客户 JSON，不读取 context、不调用安装或探测、不创建证据目录；准备 --plan 也不调用脚本。

正式安装异常、退出非零、缺失/错误结果、路径不符或超时均保留证据并阻塞环境步骤，不自动重试、切换环境或删除部分安装。已有绑定时，安装失败后再只读核验一次，确认部分安装改变环境就使绑定失效；无法核验则同时保留安装失败和探测诊断。独立资产步骤继续，精度及交付等待环境成功。输入类型错误、目录越界、共享证据损坏、证据写入失败或取消遵循现有停止边界。

历史原版安装流程的回执，只有请求、成功结果和实际绑定相互一致，且能确认原基础 Python 时才恢复安装选择。缺请求、基础 Python 无法确认或证据不一致时要求新 run-id，不从目录或评测 Python 猜配置。更早的 selection/自动发现回执仍要求新任务。绑定一旦失效，后续恢复原版本不能解除，也不能复用旧精度结论。其他依赖的完整 freeze 和源码探测不属于该绑定；原生精度执行阶段继续核验配置、数据、tokenizer、服务、API 与结果证据。

## 性能交接与依赖

默认同一主机共享 /data/evalscope-venv。需要覆盖时，通过内部 context 设置，相关精度、性能任务统一同一路径。安装和两类评测按串行约定执行，不重叠；本次没有跨插件锁或后台调度。

安装回执的 `result.performance_handoff` 保存 `benchmark_python`、`venv`、`evalscope_executable`、`evalscope_version` 和 `env.PATH`。benchmark_python 必须是安装返回、精度实际使用的 Python，不能替换为创建 venv 的基础解释器。原版安装脚本省略 --venv 时直接使用所选 Python；迁移包装层默认显式传入共享 venv，两者有意保留不同默认行为。仅共用脚本不能证明环境相同。

准备时的 PATH 只记录当时观察；性能启动前必须使用下面的只读入口重新核验，并按“绑定 CLI 目录 + 当前调用进程 PATH”构造进程环境，不重放旧 PATH 尾部。历史 performance_handoff 缺少 venv 时，只能从一致的安装请求、成功结果和实际绑定恢复，不能从 Python 父目录推测。该信息不进入客户 task.json 或三个交付 JSON。

执行前提为基础 Python、venv/pip 能力、目标目录权限、包源访问和依赖兼容性。脚本不安装驱动、CANN、Docker，也不准备模型和数据集。精度仍使用原生评测配置和评分校验；安装成功或 perf 帮助通过不能宣告精度通过。

真实安装、真实精度和真实性能分别验收。只有两端实际进程的 Python、CLI 和版本证据一致，才报告共用成功；离线替身、安装预览或三 JSON 导出均不构成实机共用验收。

## 性能启动前只读核验

总 Skill 的 [check_evalscope_handoff.py](../scripts/check_evalscope_handoff.py) 接收 --request、--work-root、--run-id、--plan。内部请求仅含以下字段；路径和摘要须替换为同一客户任务的真实回执，run-id 必须一致：

```json
{
  "customer_manifest": {
    "path": "/包外/证据根/customer-001/customer-workflow/attempts/实际尝试/customer-manifest.json",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
  }
}
```

```bash
python3 "/实际资源/scripts/check_evalscope_handoff.py" \
  --request "/包外/evalscope-handoff.request.json" \
  --work-root "/包外/证据根" --run-id customer-001 --plan
```

--plan 只检查请求形状和输出安装边界，返回 not_run，不读取 customer_manifest 或其他引用、不探测环境、不创建运行目录。去掉 --plan 后才执行正式核验：

1. 核对客户 task/run 身份、摘要引用、completed 环境步骤、正式安装请求、成功结果、安装选择和未失效的实际绑定。预览、缺证据、摘要变化或字段不一致均阻塞；不从当前 shell 或默认目录补造环境。
2. 用绑定的 Python 读取 sys.executable、sys.prefix、EvalScope 实际版本、sysconfig scripts 目录。Python 启动路径原样保留，不 resolve 为系统解释器；venv 或 CLI 被重定向到其他环境时拒绝。
3. 按当前调用进程 PATH 重新构造候选环境，解析 evalscope，要求绝对路径与绑定 CLI 相同，再调用该 CLI 的 perf --help，核对原安装脚本要求的选项。每个子进程超时 30 秒；使用 argv 列表，不执行 shell 激活。
4. 发布前重核所有来源。成功输出 status=ready、performance_handoff、sources 和 manifest；sources 使用原有 {path, sha256} 格式关联客户回执、task、安装请求/成功结果及适用的初始选择。

来源及输出边界通过后，每次正式核验在 work-root/run-id/evalscope-handoff/UUID 保存独立 request.json、handoff-check.json、proposed-environment.json、CLI 解析观察以及每个已执行探测的 argv/stdout/stderr/退出码、超时或取消信息。输出不能侵入 Skill、输入、模型/数据或 venv。原客户回执、安装选择和环境绑定保持原始字节；本入口不使旧回执就地失效，也不安装、重建、降级或重试。

确认身份变化返回 blocked、reason_kind=identity_changed、new_run_required=true，必须新任务。超时、权限、非零退出、异常输出或缺 perf 选项返回 blocked / unverifiable，只表示无法核验；无有效交接则禁止性能启动。来源缺失或不一致为 evidence_invalid。证据不可写或取消为 failed。退出码：0 为 ready 或计划 not_run；1 为证据失败/取消；2 为请求形状或输出边界无效；3 为来源或运行环境阻塞。

Agent 在性能启动前执行该入口，只使用本次 ready 返回的交接，将 benchmark_python 填入性能侧既有同名字段，并以子进程 env 合并返回的环境覆盖项（PATH 和禁止字节码写入的 PYTHONDONTWRITEBYTECODE）。argv 的 Python 使用同一 benchmark_python，不依赖 shell 激活；不把安装请求的基础 Python 用于性能进程。保存性能进程的实际启动解释器和 EvalScope 命令路径，与交接记录核对。若调用进程环境改变，重新核验；安装与两类评测继续串行。

本仓不运行性能任务，不修改性能插件；正确传递环境由 Agent 调用流程落实。ready 只证明这次环境交接就绪，perf 帮助检查也不证明精度或性能业务兼容。只有两端实际进程证据齐备才验收共用成功。保护范围为 Python/venv、CLI 路径和 EvalScope 版本；不新增全部发行包快照、依赖锁或自动还原机制，不能称为锁定全部依赖。
