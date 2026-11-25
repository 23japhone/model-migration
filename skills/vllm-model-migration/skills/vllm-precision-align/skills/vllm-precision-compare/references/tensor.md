# 张量与算子对齐的离线比较

设 `MIGRATION_SKILL_ROOT` 为完整 `vllm-model-migration` 的安装根目录。使用有 PyTorch 的本地 Python 校验采集包并执行 CPU 参考；读取 `.npy` 还需要 NumPy。下面的命令将报告写到包外。也可用 `--work-root` 和 `--run-id` 指定独立运行目录。加 `--plan` 只检查请求形状及输出位置。

```bash
python3 "$MIGRATION_SKILL_ROOT/skills/vllm-precision-align/skills/vllm-precision-compare/scripts/compare.py" --action tensor --request "$REQUEST" --output "$REPORT"
```

## 输入与范围

`plan`、`gpu`、`npu` 是绝对路径和 SHA256 的对象。提供一端时执行该端实物预检，双端齐备时逐调用比较。必填的 `tensor_alignment` 接受空对象或一对有限非负的 `atol`、`rtol`。

`tensor-plan-v1` 绑定单题 `case={subset,sample_id}`、权重身份 `model_identity`、一基输出序号 `position`、`prompt_token_ids` 和包含 prompt 及前面所有已接受 Token 的 `prefix_token_ids`。每项 `calls[]` 指定唯一 `id`、`kind=module|api`、零基 `occurrence`、两端物理 `{owner,name}` 和共同的 `slots[]`。每个槽位以唯一 `id`、`role=input|weight|state|output` 及两端实际参数路径登记。位置参数、关键字、模块参数、多返回值和原地修改后的参数分别使用 `input_args`、`input_kwargs`、`parameters`、`output`、`post_input_args` 和 `post_input_kwargs`。输出路径按实际嵌套索引取值，例如 `["output",0]`。作用域须与该题异常位置对应的实际计算相符。

- **写出前切片**。每个槽位可声明 `capture_slice={"axis":0,"start":2,"stop":3}` 或分别提供 `gpu`、`npu` 区间。相对原始形状使用半开区间，采集包同时登记 `capture_slice`、`original_shape` 和实物 `shape`。比较器复核三者，不再次截取。
- **读取后对齐逻辑布局**。需要转换时声明 `transforms={"npu":[{"permute":[1,0]}]}`，每侧也支持 `{"slice":{"axis":0,"start":0,"stop":2}}`。按列表顺序执行。映射提供方负责证明维度和有效区语义，比较器核对索引合法性和归一化后的形状。没有对应依据的融合子图或状态应进入 `scope_uncovered`。
- **有语义依据的无穷值**。仅当该槽位的数学约定允许时设置 `allow_matching_inf=true`，例如使用负无穷屏蔽的注意力分数。默认将非有限值记录为异常。NaN 始终记录为异常。

显式 CPU 参考需要与现场标量参数相符，例如 `Torch.add` 的 `alpha`。已核验的实参与参考定义冲突时，CPU 项记为 `not_evaluated`，报告指出设备和参数。两端已取得的张量仍按既定规则比较。

## 实物与来源

`tensor-bundle-v1` 以 `hardware=gpu|npu` 和 `plan_sha256` 绑定冻结计划，包含同一题、位置、前文、模型、`forward_id`、`phase`、`rank=0` 和实际来源 `evidence_kind`。从 prompt 最后一个有效位置预测首个输出时使用 `prefill`，当前单卡同步 Eager 文本贪心路径中后续位置使用 `decode`。两端的本地前向 ID 独立。阶段入口还核对第三遍的 `prediction` 与第二遍异常。独立比较入口只负责实物及数值，使用方应先完成异常复现核验。

`tensors[]` 每项指明 `call`、`slot`、相对该包目录的 `path`、`sha256`、实际 `shape`、`dtype`（如 `bfloat16`）和 `layout="strided"`。支持单张量 `.pt`（`torch.load(weights_only=True,map_location="cpu")`）及 `.npy`（`allow_pickle=False`）。离散数据保持原 dtype；量化模块需要明确作用范围内的实际编码、比例等状态。稀疏、复数和 PyTorch 量化张量在当前比较器中记为未覆盖。引用路径不得越界或经过符号链接。

`evidence_kind=runtime` 必须有绑定摘要的相对 `native_index` 引用。该文件的格式为 `tensor-native-index-v1`，记录实际设备、计划摘要、rank、前向 ID、张量清单和 `execution`。后者绑定 trace ID、引擎内部请求 ID、零基输出索引、输入 ID 与位置、实际预测行、单卡同步 Eager 和单请求条件。各张量记录包含 msProbe 的 `native_name`、`native_parent`、`occurrence`、实参 `argument` 和整个前向内的 `ordinal`。计划声明的 `scalar_args`、`arguments_complete` 也要与原生记录一致。提供 `native_frame` 时重读并核验其执行身份、有效行和张量清单。每个 runtime 包使用独立采集目录，目录内递归发现的张量文件须与清单一致。

每个 runtime 包还需由目标 worker 记录 `execution.versions`。其中 `torch`、`vllm`、`msprobe` 三个实际版本字符串须逐项匹配计划的 `runtime_versions[hardware]`。

构造的测试包请标记 `evidence_kind=synthetic`。测试包同样核对计划和逐文件字节、dtype、shape 及数值；报告保留每端来源。只要有一端使用构造数据，实机目标 `hardware_target_status` 即记为 `not_evaluated`。

## 数值判定与 CPU 参考

双端浮点比较以 GPU 数值为参考。每个有限元素满足 `abs(npu-gpu) <= atol + rtol*abs(gpu)` 时通过。等于边界计作通过。整数、索引和布尔值逐项精确核对。有效区域为空时给出未评估。两端实物各自符合声明后，规范化形状或 dtype 仍不同时，记录结构性超限。缺少浮点阈值时仍报告可测的最大绝对差和坐标。

CPU 独立对照按调用配置 `cpu_reference`。例如线性层可用 `{"op":"linear","inputs":{"x":"activation","weight":"kernel","bias":"bias"},"output":"result"}`。这些名称须绑定本调用已核验的槽位。当前数学定义和实参如下。

| op | inputs | attributes |
| --- | --- | --- |
| `linear` | `x`、`weight`、可选 `bias` | 无 |
| `matmul`、`mul` | `a`、`b` | 无 |
| `add` | `a`、`b` | `alpha`，缺省为 1 |
| `silu` | `x` | 无 |
| `softmax` | `x` | 实际使用的 `dim` |
| `embedding` | `indices`、`weight` | 无 |
| `rms_norm` | `x`、`weight` | 实际使用的 `eps`，归一化维度为最后一维 |
| `layer_norm` | `x`、实际使用的可选 `weight` 和 `bias` | `normalized_shape`、`eps`，核对输入尾部的所有归一化维度 |
| `gelu` | `x` | `approximate="none"` 或 `"tanh"` |
| `scaled_dot_product_attention` | 稠密的 `q`、`k`、`v` 及实际使用的可选 `mask` | `layout`、`kv_source`、`dropout_p`、`scale`、`mask_kind`、`causal_alignment` 均须显式填写 |

对于普通注意力，先确认所选算子边界已经取得 RoPE 等上游处理后实际参与点积的 Q/K，以及按逻辑顺序展开、覆盖完整有效前文的 K/V。当前 CPU 数学参考接受 `layout="BHSD"`、`kv_source="full_prefix"`、单请求、相同 head 数、`dropout_p=0`。K/V 序列长度须等于该位置实际前文长度，decode 的有效 Q 长度须为 1。只看到本步 K/V 写槽、分页块号、滑窗局部历史、压缩状态或尚待展开的 GQA head 时，将该 CPU 输出记为未评估。GPU/NPU 两端已取得的张量仍可逐项比较。

无显式 mask 时填 `mask_kind="none"`，并将 `causal_alignment` 指定为 `"none"`、`"upper_left"` 或 `"lower_right"`。例如一个 decode query 面对两个历史位置，右下对齐能看两个位置，左上对齐只能看第一个位置。提供 mask 时填 `causal_alignment="none"`，用 `mask_kind="bool_keep"` 表示 True 位置可见，或用 `"additive"` 表示数值加在注意力分数上。掩码须含本次全部约束且能广播到实际注意力形状。若实际加性掩码含负无穷，在该 mask 的槽位上声明 `allow_matching_inf=true`。`scale` 的数值使用实际缩放，`null` 表示标准的 `1 / sqrt(head_dim)` 缩放。参考使用 CPU FP64 的 PyTorch math SDPA，长 query 按行分块，每块保留全体有效 K/V；需要兼容 `torch.nn.attention.sdpa_kernel` 的本地 PyTorch。[PyTorch SDPA 语义](https://docs.pytorch.org/docs/stable/generated/torch.nn.functional.scaled_dot_product_attention.html)

浮点操作数在 CPU 上提升为 FP64，索引保留整型。两端实际输入、权重及所需状态逐项相等时，以同一输出分别计算 GPU、NPU 相对 CPU 的误差。输入已经不同则用两份实参各自重算，报告 `basis=per_device_inputs` 和 `different_inputs`。这项核验可指明本次调用相对其实际输入的误差。依赖范围无法核实的切片、缺少实参或尚未实现的 CPU 算子给出 `not_evaluated`。单个 `cpu_reference` 核验一个输出，其他输出在 `unassessed_outputs` 中列出。

## 回执

- 单端预检返回 `result=awaiting_peer`。检查 `capture_status=complete|incomplete` 以及 `coverage.<端>.expected_slots/verified_slots/missing/issues` 后判断能否继续。零匹配和损坏的原件均留在覆盖清单中。
- 双端结果 `result=aligned|mismatch|insufficient_evidence|not_evaluated` 仅针对 GPU 和 NPU 的数值比较。已核验的超限项保留为 `mismatch`，其他未匹配记录继续列出。双端相符时，CPU 对两端都可能报超限，独立见 `cpu_check`。综合目标见 `target_status`；共同参考、覆盖或阈值不全时，已有证据不会产生整体通过。
- 每个调用列出按输入、权重、状态、输出汇总的 `roles`、逐槽 `slots`、原件 `sources` 和双端执行序号。数值报告包含最大有限绝对误差、坐标、两端值、首个超限、超限数和比例、非有限计数及首个非有限位置。非有限数值使用字符串 `nan`、`inf`、`-inf`。双端可核实的实际调用顺序一致时，`first_exceedance` 指出已比较范围内最早的超限调用。`largest_error` 单列最大有限绝对误差。
- 正常写出报告返回 0；非法请求、计划或顶层来源身份错误返回 2。单个文件缺失或损坏写入覆盖清单，同一请求仍可查已完成的比较。
