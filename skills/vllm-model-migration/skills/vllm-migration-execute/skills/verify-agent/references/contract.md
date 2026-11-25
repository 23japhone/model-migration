# 请求、结果和资源交接

脚本在目标执行主机运行，Python 3.10+，自身仅依赖标准库。目标解释器需要实际 vLLM/vLLM-Ascend/torch-npu；Docker 方式另需 Docker CLI，NPU 和监听者归属检查需要 Linux /proc 读取权限及 npu-smi。执行命令使用 argv，不执行拼接 shell。HTTP检查在独立CLI主线程使用POSIX总时限，覆盖慢速头部/正文并恢复原timer；不在后端线程中嵌入本模块。

## verify 输入

顶层 schema_version=1.0，必需 model、runtime、service、registration、npu、required_features、cases；可选 build、handoffs、api_key_env、worker_capture。未知字段拒绝。路径均为目标环境绝对路径；模型清单及构建清单使用根内相对路径。

| 对象 | 必需内容与边界 |
|---|---|
| model | name（实际 served model name）、root（宿主资产根）、runtime_root（载体资产根）、files（相对路径→SHA256）、weight_files（全部权重文件）、config（原始配置相对路径）。全部权重及 index、config 必须固定内容，不改架构名 |
| runtime | kind=docker/process、python（实际解释器）、versions（安装包名→准确版本）、files（载体源码绝对路径→SHA256）、env（非凭据环境）；可选 cwd。源文件清单至少包含注册检查所用 implementation 和 registry |
| service | ownership=owned/borrowed、endpoint（执行主机有效 IP/localhost 及显式端口，运行时核对本地归属）、retention=keep/stop、timeout_seconds（1–3600）、weight_markers（实际日志中的真实权重加载标记） |
| 新建服务 | service.command 为明确 argv。process 在目标解释器环境启动独立进程组；docker 创建 host network 载体，先复验再执行命令并保存日志。须显式model路径、served model name、host和port，前三者分别绑定runtime_root、name和端点端口；支持--model与vllm serve位置参数。不添加 trust_remote_code、量化、graph 等隐式能力 |
| service.docker | owned Docker 必需 image_id=sha256:完整ID、name、devices（显式/dev路径）、mounts（source/target/read_only）、memory、shm_size、cpus。只读挂载模型及必需驱动；不要默认传入全部卡。计算节点/dev/davinciN与声明的物理编号必须一致，不接受节点重映射 |
| 复用服务 | service.identity：Docker 使用完整 container_id、image_id、started_at；process 使用 pid、start_ticks、pgid。process 另提供 log_path，Docker 可选 log_path，否则读取容器日志。borrowed 的 retention 只能 keep |
| registration | resources 指向完整总 Skill 根或其直属 skills 目录，也兼容已有平铺资源根（含 vllm-model-register/scripts/check.py）；嵌套布局从所给副本的 references/skill-layout.json 定位入口，按总入口静态资源规则枚举并记录实际身份，复制前后核对源、目标的集合和摘要，不读取构建清单。平铺目录继续支持普通文件，排除缓存、日志和维护项，拒绝链接及特殊文件。request 指向目标载体的公开 check 请求。核对原始 architecture、运行 config 路径及 implementation/registry 内容身份，复制资源而不挂载开发 SDK，执行公开 check.py 并核对本次 JSON 回执 |
| npu | devices 为唯一的宿主物理卡编号数组；可选 allocation={path,sha256} 绑定 completed allocate 回执。仅校验后的共享回执允许同卡其他任务；无回执保持独占。本载体越界用卡始终阻塞。最终载体实测物理→逻辑→进程索引，核对集合与顺序，详见下节 |
| required_features | 必需 text/chat/stream/image，用 required=true 的用例覆盖；image 至少两个内容不同的内嵌图片用例 |
| cases | id、kind、route、payload、semantic、required；可选 min_prompt_tokens、expected_prompt_tokens。text 对应 /v1/completions；其他为 /v1/chat/completions。payload.model 必须匹配 name，n=1；stream 为布尔值。图片用 data:image/png/jpeg/webp;base64 URI 固定内容 |
| semantic | mode=exact，expected 为预先确定的完整答案数组；或 mode=review，在读取完整内容及原始问题/图片后独立复核。exact 不等于正式数据集评分 |
| build | 仅 owned Docker：context、files（整个context全部文件SHA256）、dockerfile（清单内相对路径）；可选 builder=default/legacy，仅显式legacy才对该命令设置DOCKER_BUILDKIT=0，不修改全局环境或失败后自动切换。校验后一次构建，使用实际 --iidfile 结果覆盖无build时的image_id输入，不依赖可变tag。SDK/适配源码及原Dockerfile的构建选择由授权运行输入明确提供 |
| handoffs | 可选 prepare/model/image/precheck/adaptation/registration→{path,sha256}，核对对应 task_id 及完整 1.0 completed 共同外层；precheck 另绑定原始 config、版本及允许路线，并保留来源；registration_required/adaptation_required 的静态 support=review_required 仅在对应 completed/pass 注册/适配交接齐备时允许，其他失败或待复核结论仍阻塞；不能代替当前资产/载体/注册/NPU/请求核验。原始支持度/适配允许范围仍须按Skill步骤核对 |
| api_key_env | 可选目标执行环境已有 API key 环境变量名，认证值只在请求时读取；不得将 token/password/API key 写入 env、argv 或报告 |

--plan 只检查请求形状和输出边界；不验证资产是否存在或支持情况，不写目录、不安装、不构建、不启动。运行目录为 work-root/run-id/verify-agent/独立attempt；不得位于技能、资产或构建context内，不覆盖旧尝试。

预检目标 vLLM 与 `runtime.versions.vllm` 的比较沿用迁移阶段规则：目标不含 `+` 时，仅对比运行版本中 `+` 之前的完整公开版本；目标包含 `+` 时，要求完整字符串一致。例如 `0.20.2` 接受 `0.20.2+empty`，`0.20.2+empty` 仅接受同一完整版本，不接受 `0.20.2` 或 `0.20.2+other`。`rc`、`dev`、`post` 等部分不被去除，空值或缺失版本拒绝。

这只用于目标匹配，不改写输入、冻结版本或镜像观察。`runtime.versions` 保留安装包完整版本，最终载体探针仍逐项精确核对完整版本及源码/模型文件内容；镜像和容器仍绑定完整 ID。准备配方与目标声明继续精确一致，不能借版本匹配规则绕过冻结或替换运行构建。

所有操作均保护完整总 Skill 根，包括嵌套 Skill 及共同契约，不依赖插件标记。verify的计划根据显式请求核对模型/注册资源/构建根；review、stop及inspect-devices的计划不读取来源交接，实际操作读取并绑定来源后核对已声明的保护根。路径无效不得执行停止效果。

## msProbe 采集准备

使用[采集请求示例](../assets/worker-capture.request.example.json)在 verify 输入中声明可选的 `worker_capture`。Eager 服务使用 `adapter="msprobe_pytorch_eager"`。单卡图服务使用 `adapter="msprobe_pytorch_aclgraph"`，部署选项和会话范围见[图模式采集](runtime-capture.md#单卡-aclgraph-文本请求)。两种适配器均须声明宿主绝对路径 `output_root`，可选 `expected_msprobe_version` 精确匹配目标解释器安装的 `mindstudio-probe` 分发包版本。未知字段、非法类型和相对路径按输入无效处理。普通部署可省略整个对象。可选 `decode_cache={"max_snapshot_bytes":16777216}` 设置一次前向内所有快照共用的字节预算。全层观察使用 `layer_scope=all`（默认）。单卡 Eager 的旧式单步观察可选 `legacy_pair`，ACLGraph 的缓存观察要求 `all`。完整模型应按实际数据量设置预算。迁移阶段照常引用该请求文件及其 SHA256，公开的 `--plan` 只检查请求形状。

采集服务须为自有。Eager 适配器要求显式 `--enforce-eager`，支持单 NPU 或单机双 NPU 纯 TP=2。ACLGraph 适配器要求单 NPU，启动时省略 `--enforce-eager`，通过 `--compilation-config` 指定 `FULL` 或 `PIECEWISE`。按声明及目标环境核对并行设置。已有 `--additional-config` 指定的 `dump_config_path` 或目标环境的 `MSPROBE_CONFIG_PATH` 会产生配置冲突。Docker 请求须在创建容器前显式声明可写 bind mount；`output_root` 可为挂载源或其子目录，覆盖本次尝试的映射须唯一、未被其他目标挂载遮蔽。Linux process 使用同一宿主路径。输出根须在 Skill、注册资源、模型和构建输入之外。

注册复验后、模型启动前，在 `output_root/<attempt_id>/msprobe.json` 生成 PyTorch 配置。Eager 的 `dump_path` 指向载体视角的本次 `dump/`，ACLGraph 指向 `dump/native/`。Eager 配置保存模块和受支持 API 的前向输入输出（`task=tensor`、`level=mix`、`risk_level=ALL`、`tensor.data_mode=["forward"]`），初始 `dump_enable=false`。短探针使用目标 Python、有效环境和载体身份，导入 PyTorch 调试入口，调用目标版本的配置解析器并核对结果。探针将唯一校验内容写入预定输出目录，宿主读回一致后移除校验文件。准备阶段封存配置和适配器源码摘要。服务启动时加载适配器；模型、Eager、并行和调度参数沿用原请求。

每次声明采集的 verify 尝试在其证据目录生成 `worker-capture-preparation.json`。其中有 `run_id`、`attempt_id`、适配器、目标解释器；随进度记录实际版本、`config={path,runtime_path,sha256}`、`dump={path,runtime_path}`、探针观察及读写结果。宿主消费者用 `path`，worker 用 `runtime_path`。结果见 `verification-manifest.json` 的 `detailed_report.worker_capture={status,reason,receipt:{path,sha256}}`，不参与普通功能成功判定。已完成最后一次 worker 身份复核的部署交接也包含该字段，和 `final_service` 一起写入后再计算交接摘要。

| status | 可依据的证据 |
|---|---|
| `prepared` | 目标解释器解析了初始关闭的配置，宿主确认载体可写入指定目录。复用在线服务前同时核对最终部署成功及 `final_service.state=retained`。worker 挂接与正式采集另行验收。 |
| `unavailable` | 采集准备不适用或执行失败。按 reason 排查模式、包、配置、挂载或读写问题，服务继续原部署流程。 |
| `not_attempted` | 前置部署步骤失败，报告保留阻塞原因。 |

复核顺序为最终验证报告中的 `handoff_sha256` → 部署交接中 `worker_capture.receipt` 的路径与 SHA256 → 准备回执中 `config` 的宿主路径与 SHA256。服务停止、配置改变或 worker 身份变化后，回执仍描述原尝试的准备结果。取消和最终证据写入失败按 verify 原有失败规则处置。

## 设备编号与有效运行环境

共享分配的算法、5%预留和容量不足用户决策见[共享部署契约](../../../../../references/customer-observations.md#空闲优先与共享部署)。allocation 在 --plan 中只检查引用形状；执行时验证摘要、观察、配方、逐卡预算、选卡和 argv 一致性。创建载体前及启动模型前分别观察最新容量，不改变已确定比例。原全空闲方案出现外部进程须重新规划；驱动或容器共享失败保持原诊断。运行后复核及导出只核验分配证据和服务归属，不把已加载权重后的剩余显存再用作启动门槛。

首版适配单机 Linux、Ascend 910B 整卡及可识别的 npu-smi NPU/Chip 表；其他型号/虚拟设备/未知布局明确阻断，不推算编号。Docker 必须通过当前执行主机的本机 Unix socket 访问；控制入口在服务器宿主侧读取 /proc 与 npu-smi。不能在 macOS 或隔离的 PID 命名空间中用本机观察冒充服务器证据。

解析映射表时去除 Chip Name 字段两端的格式空白，再判断 MCU 跳过项和计算芯片型号；空格、制表符及 LF/CRLF 换行不改变映射结果。未知型号、非法计算 Chip/逻辑编号、重复映射仍阻断，不通过忽略异常行继续部署。

- owned Docker 创建时 ASCEND_VISIBLE_DEVICES 选择物理卡；省略则由 npu.devices 生成，显式值必须选择同一物理集合。仍只传选定 /dev/davinciN 和已声明管理节点，不扩大设备或权限。
- 独立探针在最终载体、目标 Python 中加载 libascendcl.so。基础探针仅在自身环境中移除 ASCEND_RT_VISIBLE_DEVICES，使用 aclrtGetLogicDevIdByUserDevId、aclrtGetPhyDevIdByLogicDevId、aclrtGetLogicDevIdByPhyDevId 查询并双向核对。记录返回码、实际库路径/摘要、AscendCL API版本、解释器和环境；不设置设备或分配设备内存。
- owned 服务未显式提供 RT 时按选定物理卡顺序生成逻辑编号；已有 RT 按逻辑号验证并保留顺序，冲突阻断。第二个探针在最终有效环境下验证进程索引。短任务和模型启动均显式注入相同环境；不靠 Docker 客户端自身的环境传递。
- borrowed 服务只读取实际 API 进程的声明环境键和设备键，不改写服务环境；若实际未设置 RT，不补写一个虚构的启动值。容器基础可见范围可大于本次选择，进程实际可见及本载体实际用卡集合仍须与选卡一致。
- 所有宿主计算进程均参与归属检查，保留 NPU ID 和 Chip ID，使用完整CID/cgroup或PID/starttime/进程组核验。每张选中卡必须有本载体worker；独占方案拒绝同卡外部任务，校验后的共享方案将外部进程单列，不纳入服务身份签名或清理对象。外部进程正常变化不单独使共享复核失败。本载体不得越界用卡，worker加载的CANN库须与探针对应。真实功能前后及观察结束时仍复核本服务PID/启动身份。

原请求及 handoff.runtime 保持声明内容。attempt内保存 declared-request.json；不可变设备观察目录保存 device-mapping.json、effective-runtime.json及基础/最终探针。handoff新增 declared_request、device_mapping、effective_runtime 三个 {path,sha256} 引用。映射绑定原请求、宿主hostname/boot_id、完整资源与启动身份、选中物理卡、三类编号、CANN库及解释器；有效环境只允许按映射补全设备变量。公共静态校验归总Skill的 scripts/device_contract.py，消费者不得私自重写原请求。

接口依据：[物理→逻辑](https://www.hiascend.com/doc_center/source/zh/canncommercial/900/API/runtimeapi/aclcppdevg_03_2059.html)、[逻辑→物理](https://www.hiascend.com/doc_center/source/zh/canncommercial/900/API/runtimeapi/aclcppdevg_03_2060.html)、[进程索引→逻辑](https://www.hiascend.com/doc_center/source/zh/canncommercial/900/API/runtimeapi/aclcppdevg_03_2057.html)。文档支持不等于目标镜像已通过实测；实际缺失接口或非零返回码阻断，保留原始诊断。

## inspect-devices 只读复核

请求为 {handoff:{path,sha256}}，指向新的 deployment-handoff.json。--plan 只检查形状。实际运行校验完整证据链、宿主与载体身份，扫描全部物理卡的计算进程，重新探测最终环境映射并比较PID/starttime；输出独立 verification-manifest.json，operation=inspect-devices、conclusions.devices=pass 才算通过。旧交接缺映射时 blocked/退出3，要求重新执行verify取得证据，不能凭旧设备数量补齐。接口缺失、映射变化或归属错误保留失败回执；取消为failed。

本动作不复跑功能、注册或精度，不启停模型，不改写旧交接；owned和borrowed都只读处理。精度任务通过此公开CLI在评测前后复核。快照证明观察窗口内的实际使用，不能表述为服务永久不会变化；容器重建、宿主重启或库身份变化后须重新验证。

## 功能及生命周期

先核对资产，再纳入载体并核对版本/逐文件内容/原生NPU，再调用注册公开检查，随后启动或核验服务。readiness 核对唯一served model name和model card的实际root，必须绑定到固定资产根；进入功能前还须匹配监听socket、实际API/worker解释器和NPU PID/cgroup，读取真实加载日志。HTTP非200、错误model、空生成、多choice、不完整finish_reason、SSE错误/截断/结束后数据均不能通过。单用例失败不跳过其他独立用例。

共同结果含 inputs、status、conclusions、evidence、uncovered，兼容旧 success/summary/detailed_report/report_path。执行 completed 只代表测量结束，success 仍要求每项必需结论 pass；语义 review_required 时 success=false，退出1。blocked 退出3，输入无效退出2，执行/证据失败退出1。每次结果保留原始请求、HTTP/SSE、组装文本、逐项结果和精确资源身份。正式精度与性能不写成通过。

JSON与SSE共用usage校验：可省略或为null，提供时必须为对象；已提供的prompt_tokens、completion_tokens、total_tokens须为非负整数，布尔值不能冒充计数。SSE保留最后一个非null且经校验的usage；min_prompt_tokens须为非负整数，声明正门槛时必须由实际prompt_tokens满足。expected_prompt_tokens为可选正整数，声明时要求实际usage.prompt_tokens完全相等，用于发现原生模板观察与服务分词不一致；缺失usage也拒绝。无效usage只使该用例失败，保存原始响应及错误，继续其他独立用例。测量失败时仍按明确的资源策略交接，不将completed或keep解释为功能通过。

owned 服务失败或取消只处理已核对的自有资源；成功默认 keep，临时 stop 策略明确记录。borrowed 始终保留。创建过程取消会按创建意图的唯一 owner nonce、image、完整CID及名称进行一致核验；不以名称单独证明归属。停止不删除镜像、容器或运行证据。SIGTERM/SIGINT视为取消；目标短任务使用Linux subreaper收养派生进程，按PID/starttime取消自有任务树，再结束Docker客户端；独立SDK子进程创建新session也不能绕过任务回收。

最终清理或独立stop期间取消，记录原始取消诊断，在暂缓后续SIGINT/SIGTERM的区间只恢复一次精确身份清理；沿用已有停止/命令时限，不无限重试。恢复失败明确记录cleanup_error，不能假称释放；borrowed只重核交接。被取消操作始终failed且success=false，即使清理已确认，lifecycle也仅描述资源结果。终态写入期间收到取消也归档failed，并恢复原信号处理器。服务最终状态、取消、恢复错误及暂缓信号分别保留。

## review 和 stop 输入

--action review 的请求为 {source:{path,sha256},cases:{用例id:{verdict:pass/fail,reason:中文理由,response_sha256:摘要}}}。source 必须是本次 verify completed、除 semantic 外全部必需结论 pass 的不可变报告；原报告七类必需结论必须齐全，原请求与结果的必需用例须一一对应且协议通过；全部必需响应、原始cases清单与绑定SHA256的部署交接证据必须完整。只完整复核所有必需且待复核用例。复核时重新校验响应与原HTTP/SSE摘要，生成新报告，原报告不变。该操作不刷新运行态，不重复启停服务。

--action stop 的请求为 {handoff:{path,sha256}}，指向 deployment-handoff.json。仅接受 owned，复查完整资源及启动身份，Docker 还须 owner nonce 匹配；PID/CID或启动身份变化则拒绝。stop 只对 lifecycle 给出结果，不宣称重新验证了功能。停止后的证明保留为独立结果。

process长期服务的主PID已退出而原组号仍有成员时，不能以成员启动时间较晚推断归属；拒绝发信号并报告清理未完成，保留诊断供授权人工核对。组内没有成员时直接记录已停止，不再向旧组号发信号。短检查任务另由仍存活的subreaper管理，不依赖此推断。

多机、bridge network、非Linux进程归属和容器外调度未实现。历史精度目录的启动脚本不属于本次新链，不能调用它们建立第二个生命周期所有者；其精度入口迁移留待对应Skill范围。


## Eager 单请求采集与回查

在最终执行主机上调用同一个 `scripts/verify.py`。对 `--action capture-one`，请求示例如下。

```json
{
  "source": {"path": "/work/run/verify-agent/attempt/verification-manifest.json", "sha256": "<报告的 SHA256>"},
  "request_id": "sample-001",
  "prompt": "1, 2, 3,",
  "max_tokens": 2,
  "output_index": 1
}
```

入口沿报告、最终交接和准备回执校验配置，要求自有且 retained 的单 rank NPU 服务。实际 runner 须为同步 Eager，服务由 Python 模块入口或 `vllm serve` 启动。未匹配的运行源码、缺少进程就绪记录、提前 EOS、混入其他样本及未覆盖的解码层会留下失败原因。首次执行包含一次关闭采集的同参数对照请求。可选 `allowed_token_ids` 对两次请求采用同一选词限制，并核验指定输出的分数确有变化。

全部文本解码层由实际模型和配置层数共同确定。每次前向记录实际输入行、位置、logits 行、msProbe step 和 rank。每个产生输出的前向保存独立的全词表 logits 与 greedy 选词分数。非空张量校验原始文件；msProbe 对零元素结果只记录 shape 和 dtype。索引保留 msProbe 支持且实际调用的模块/API、父调用、参数位置、dtype、shape、原生张量文件及 SHA256；文本请求不会经过视觉编码器。`capture-receipt.json` 给出离线回查入口，部署交接保留原摘要。

`--action capture-read` 使用采集回执作为 `source`，再提供 `request_id` 和零基 `output_index`。返回实际前文、两份完整向量、候选和已接受 ID 以及同次前向的张量引用。默认返回所有调用；可用 `layer`、`api`（如 `Functional.linear`）和层内零基 `occurrence` 选取一次调用。非有限分数在 JSON 向量中以字符串表示。使用 `torch.load(path, map_location="cpu", weights_only=True)` 打开 msProbe 的 .pt 张量。两种操作沿用 `--request`、`--run-id`、`--work-root` 和静态 `--plan`。

全程回执也可选择 `view="timeline"`、`forward_id` 或零基 `prompt_position`，与 `output_index` 互斥。时间线给出半开位置区间及可空的输出序号，前向和位置查询沿用层及快照选择条件。见[完整请求回查](runtime-capture.md#从完整请求回查每次前向)。

针对已采集缓存的前向，查询还返回 `decode_cache`。可选字符串 `cache_snapshot` 使用具体快照名，例如 `ssm.after`，返回该张量完整的 `values` 和 `shape`。可用零基 layer_index 或完整 layer 路径定位实际层。全层模式另支持 input、output 激活快照；普通注意力和 GDN 的快照名见[单步缓存查询](runtime-capture.md#从一次-decode-回查层缓存)。单卡 Eager 的压缩 KV 和 RoPE 写后值分别使用 `mla.latent.after` 和 `mla.rope.after`。[MLA 缓存字段](runtime-capture.md#单卡同步-eager-的-mla-缓存)

## 正式评测采集会话

`verify.py --action capture-session` 沿用 request/work-root/run-id/plan。begin 请求字段为 operation=begin、source 部署报告摘要引用、owner_pid；返回不可变 session 引用，租约绑定 PID/启动身份、部署、派生配置和 nonce。其余操作的 source 为 session 引用：arm 必填 sample_key/native_id/trace_id，可选正整数 cache_output_index 或 `cache_capture={"mode":"full_request","max_request_snapshot_bytes":正整数}`。后者要求部署使用全层缓存观察；seal 必填 trace_id 与 http.request/http.response 原始字节文件 `{path,sha256}`；end/status 无其他字段。--plan 仅校验形状，零目标环境访问和写入。

begin 核验自有 retained 服务、新适配器真实 worker/engine/frontend 就绪记录与摘要；prepared 不等于 ready。跨命令通过文件锁保护持久 lease 状态，不依赖上次命令持有的进程锁。旧 capture-one 在租约存在时也拒绝。重复 arm、已封存 trace、其他控制者或失活租约不覆盖现场。

前端从实际 engine_client.generate 参数取得 external ID，经 InputProcessor 记录 engine internal ID，并记录完整响应 ID 与最终引擎 Token 序列；不会将 Chat 请求套成固定 cmpl 后缀。worker 前向覆盖模型执行至 compute_logits；关闭 debugger 后复制原始全词表 logits，argmax 前复制处理后分数，调度器输出为最终接受依据。每个已开启前向都有 begin/end。seal 核对正式响应、接受序列、前文、所有 Token 前向、实际模块/API 调用与文件后生成 capture-receipt。EOS 短输出允许封存，不伪造第二个 Token。

单卡文本 greedy 会话支持同步 Eager 和 ACLGraph，图服务可回查每次实际重放。双卡 TP=2 的同步 Eager 会话还可采集单图、SSE 和显式 seed 的随机采样。[单卡图服务](runtime-capture.md#单卡-aclgraph-文本请求) · [双卡生成与回查](runtime-capture.md#双卡图文流式与固定-seed-采样)

msProbe 的覆盖范围为实际模块与注册 API；不保证设备 kernel 内部或未注册扩展内部可见。hook 包及目标源码摘要仍须匹配。采集失败不更改普通部署生命周期结论；会话操作自身失败返回 1，输入错误 2，成功 0。end 仅释放本次会话，未完结实际请求保留租约；不停止模型或其他任务。采集回执与查询产物独立于部署交接，后者不回写。

单机 TP=2 的全程采集使用 `capture-session`。封存回执包含一次逻辑前向及两张卡的逐层原值索引。按输出或 prompt 位置读取时可指定 `rank`。设备、分片及快照预算的语义见[运行时采集](runtime-capture.md#单机-tp2-联合采集)。
