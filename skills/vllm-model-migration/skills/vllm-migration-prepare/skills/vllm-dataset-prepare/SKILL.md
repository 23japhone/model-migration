---
name: vllm-dataset-prepare
description: 适用于为华为昇腾上的 vLLM-Ascend 后续评测准备 GSM8K、C-Eval、MMLU 或通用数据，支持 HTTPS 下载、本地复用、安全解包与格式核验，记录 split、原始样本位置和内容身份，不安装或运行评测器。
---

# vLLM 数据集准备

交付可访问的原始数据、文件身份与样本选择记录。文件格式通过不代表评测精度通过；不修改原题、答案、评分规则或现有评测脚本。

## 输入与范围

明确执行主机、来源、资产 path、配方或通用格式、split 与样本范围。使用[请求与交接契约](references/contract.md)；预置来源和格式依据见[来源与配方](references/sources.md)。

`selection.limit` 是用户可选的正整数上限；未填写或 null 时选择 offset 之后的全部候选记录，默认 offset=0 即所选 split/科目全量。上限大于可用数量时只选择现有记录。对接 EvalScope 时按单个原文件/子集分别准备并使用相同上限，不把跨文件合计上限误作每科上限；全量或前 N 行均使用 offset=0、无随机 seed，并把实际记录数量交给评测请求生成 sample_ids。

| 配方 | 原始格式与必需布局 |
| --- | --- |
| `gsm8k` | train/test JSONL，question/answer 保留原始答案分隔标记 |
| `ceval` | 各科目 dev/val/test 表头 CSV；test 可没有公开答案 |
| `mmlu` | 各科目 dev/test 无表头六列 CSV |
| `generic` | 用户明确的 JSONL、JSON 对象列表或 CSV，可指定必需字段和文件 |

预置配方提取已有数据下载配方，使用相同发布包的 HTTPS 地址并核对官方加载器要求；不能只准备评测 split 后称现有加载器可用。selection 只生成原文件和行号的选择清单，准备资料保留配套 split，不裁剪或重写原始数据。

指定 path 不存在时，Agent 继续查阅官方数据来源；若官方提供默认本地数据位置，先把实际绝对路径和已读资料的 path/sha256 交给内部 default_local，来源用 recipe 或 http，path 仍保留客户指定位置。准备工具先尝试读取默认位置，有效则复用，明确不存在才下载到客户 path。目录存在但读取失败时保留原因，不覆盖或当作缺失下载；无官方默认位置则使用原下载流程。不能根据数据集名猜 SDK 缓存目录，不要求客户填写 default_local。完整规则见[指定路径缺失时的来源选择](references/contract.md#指定路径缺失时的来源选择)。

## 工作流程

1. 从[三配方示例](assets/gsm8k.example.json)、[C-Eval 示例](assets/ceval.example.json)、[MMLU 示例](assets/mmlu.example.json)或[通用本地示例](assets/generic-local.example.json)生成请求；替换 path、split、科目和样本范围。不要把例子的样本数量当作正式精度默认值。
2. 先执行 `--plan`；只核对请求、静态配方和输出路径，不联网、读数据内容、解包或写运行证据。
3. 在数据所在环境运行 `<技能资源目录>/scripts/prepare.py`。远程主机使用该主机实际路径；完整复制所需资源并核对摘要，不依赖维护仓库或专用调用工具。
4. HTTPS 下载保留原始 payload 和来源记录，既有下载、解包文件须通过大小和 SHA256 后复用。URL、revision 声明、摘要、解包内容或缓存路径冲突则失败，不覆盖、不删除已有数据。网络失败保留部分文件和独立尝试证据。
5. 任何来源（含 Hugging Face 或 ModelScope 的 HTTPS 数据链接）发生网络错误时，按[网络异常与代理](references/sources.md#网络异常与代理)核对原因。确认是代理缺失、代理配置错误或当前网络需要代理访问后，提示用户在实际下载主机为执行进程配置或修正网络代理，等待条件就绪再继续该来源；不能仅凭一次超时认定代理问题。
6. 下载重定向的每一跳均只允许 HTTPS，在发送降级请求前拒绝；保留标准 TLS 核验与环境代理处理。ZIP/tar 先核验全部成员，拒绝越界路径、重复路径、链接、特殊文件、布局冲突及过大内容，再在新的目录保留原布局解包。格式不支持时明确失败，不通过修改题目或答案使其通过。
7. 核对清单中的实际路径、来源、文件身份、required_splits、selection、未覆盖项和结论，将绝对证据路径交给后续调用方。

```bash
python3 /实际技能路径/scripts/prepare.py --request /实际请求路径/data.json \
  --work-root /用户指定证据根 --run-id customer-run-001 --plan
# 核对后去掉 --plan，执行本次已授权的数据准备。
```

## 完成与交接

`status=completed` 且 `conclusions.assets=pass` 表示本次范围的资产核验完成，`evaluation` 始终未评估。数据身份和样本选择身份分别记录；GPU/NPU 比较时核对两者及实际 split、科目，不能仅比较数据集名称。

结果采用随包的[组级结果 Schema](../../../../references/task-result.schema.json)。证据保存在包外，每次新建尝试目录。选择清单不自动修改或接入评测器；调用方须明确应用相同行号范围，不能把下载完成或选择已记录当作实际评分已执行。

本地内容不能证明上游来源；HTTPS 发布包的 branch、ETag 和用户 revision 声明不能代替不可变 repo commit。使用原始 payload SHA256、解包清单和可选预期摘要固定本次字节，保留版本证据限制。本任务不安装 AISBench、启动模型服务或操作 NPU。

## EvalScope 原生目录交接

C-Eval/GSM8K 交给 EvalScope 时使用 layout=evalscope，自动在包外准备独立评测目录。C-Eval 自动生成各科目 configs 元数据；GSM8K 隔离 socratic 变体，并生成默认 main 配置，使 default/main 选择同一份 train/test 文件，README 同样纳入文件摘要。题目与答案文件字节不变，来源目录保留。以新回执 asset.path 为评测路径，之后调用精度任务的 --action preflight，不能手工把 local_path 改到未验收的父目录。原始 raw 布局仍为独立准备入口默认值；文件验收不代表原生加载、推理或评分已通过。子集和题数的区别见[客户字段契约](../../../../references/customer-task.md#gsm8k-子集与题数)。
