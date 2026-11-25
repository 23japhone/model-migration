---
name: verify-agent-plus
description: 在已有 vLLM/vLLM-Ascend 服务上执行数据集精度测试（默认 EvalScope，兼容 AISBench）、复核历史结果、比较基线和判定目标时使用。也可按选题清单用 EvalScope 复测，将同一请求的评分与 Token 预测关联。
---

# 数据集精度测试

使用实际评测配置执行评测，新请求默认 EvalScope，分别交付测量结果、评测有效性、比较条件和目标判定。数据集由请求选择；示例数据集不构成名称白名单。完整回答的语义检查不能代替数据集规定的评分方法。

客户流程先由准备阶段每次调用原版安装脚本，精度消费其实际 Python 与 EvalScope 版本，不另查 latest。安装 perf 帮助检查不证明精度兼容。用 scripts/catalog.py 的 metadata_only=true 先发现原生子集；准备后携带 dataset_manifest 引用重新取得真实样本数及 tokenizer 长度，绑定最终数据目录；不推理或评分。调用与映射见[客户观察契约](../../../../references/customer-observations.md)，不能省略子集或替代原生评分器。

catalog 发现指定路径不存在时仍由 Agent 查官方来源；source_resolution 要求先核对官方默认本地位置。Agent 将有依据的候选及资料引用交给 dataset-prepare 的 default_local，先尝试读取，明确缺失后下载到客户原路径；catalog 自身不联网下载。metadata_only 也返回此缺路径指引，不把原生科目 metadata 当作本地数据已就绪。

EvalScope 配置中的 `limit` 可选，由用户输入正整数作为每个 subset 的题数上限；省略或 null 表示所选 split/subset 全量。超过实际题数时只测现有题目，拒绝 0、负数、布尔值、字符串和小数。数据选择与 sample_ids 必须按实际行数生成，并与原生 loader 逐题核对；不能把未填写视为待确认、默认小样本或改动已冻结基线。这里的 limit 不控制回答长度，max_tokens 仍按原规则显式配置。

EvalScope 输出触及已配置长度上限时，保留原生评分和全部样本，记录 `output_truncation`，不单凭截断判整个评测失败。原生 `max_tokens` 必须与 HTTP `length` 对应，生成 token 数须等于请求上限；推理错误、覆盖、内容一致性、上下文及其他校验照常生效。仅记录截断不代表样本答对或模型达标。AISBench 结束策略保持不变；冻结基线及历史失败报告不改写。

当任务质量对齐交接失败题号时，`scripts/validator.py --action token-run` 执行已选题的原生重跑，绑定同一请求的 Token 预测及逐题评分。见[定向重跑契约](references/contract.md#token-定向重跑)。

## 执行步骤

1. 阅读[请求与结果契约](references/contract.md)。取得已完成的部署交接、数据准备清单、实际服务模型名、评测环境及配置。仅有模型名、HTTP 200 或旧分数不足以开始正式精度验收。
2. 优先复用已有服务。缺部署或数据时，向调用方交接给 `verify-agent` 或 `vllm-dataset-prepare`，不运行本目录的历史启动脚本，不自行下载或安装。评测环境的明确准备方法见[环境与来源](references/sources.md)。
3. 将请求写在包外，用公开 CLI 的 `--plan` 检查形状和输出位置。计划不会解引用交接、执行 Python 配置、访问服务或写入运行文件；它不证明输入资产和环境可用。
4. 先用 `--action preflight` 对同一请求执行无推理数据预检；原始配置可省略 local_path，由准备回执绑定，显式冲突不覆盖。预检通过只表示数据与原生加载一致。`run` 核对实际权重和数据内容，并在观察服务前自动重做该预检，随后核对既有服务身份、监听者、worker 和服务模型卡；在评测解释器内核验请求版本、实际配置及样本映射。直接精度请求省略 version 或使用 latest 时读取 PyPI；客户流程传入准备阶段安装回执中的实际版本，不重新选择 latest。明确版本与实际安装不符、latest 查询失败或配置不兼容时阻塞并保存原因；不额外要求该版本为 latest。本任务不安装依赖。原模板、预处理、生成参数和评分器保持原配置，运行副本只改变输出位置及本次回环记录端点。
5. 每项评测经本次自有 HTTP 记录器透传，保存请求及响应原文，再读取原生 predictions、评分及逐样本详情；EvalScope 同时核对 reviews 和原生报告。逐行核对 reader 内容与原始选择，并从原始 HTTP 字节检查覆盖、退出状态、错误、token 统计和生成结束；缺结果不填零，不以退出0或报告存在代替有效测量。某项失败保留诊断，其他独立项继续。
6. 需要比较时先核对两端评测工具、工具版本、基准评测版本、权重、数据、选择、模板、评分、生成参数和已采集服务默认设置。GPU 基线可为固定报告或独立基线请求；来源不完整时保留单端结果，不声明对齐。
7. 根据明确指标、单位和方向判定目标。测试完成但未达标时交付 `completed` 与目标 `fail`；不改参数重测，不进入诊断或修复。缺目标不套用固定阈值。
8. 交付独立尝试目录及“真实验收、离线测试、未验证项”，检查服务身份是否保持一致。精度任务只回收自有评测进程和记录器；服务始终由原部署任务管理，不能按进程名清理。

## 公开入口

```bash
python3 /完整资源/skills/vllm-model-migration/skills/vllm-precision-align/skills/verify-agent-plus/scripts/validator.py \
  --request /包外/request.json --work-root /包外/runs --run-id precision-001 --plan

python3 /完整资源/skills/vllm-model-migration/skills/vllm-precision-align/skills/verify-agent-plus/scripts/validator.py \
  --request /包外/request.json --work-root /包外/runs --run-id precision-001

python3 /完整资源/skills/vllm-model-migration/skills/vllm-precision-align/skills/verify-agent-plus/scripts/validator.py \
  --action analyze --request /包外/analyze.json --work-root /包外/runs --run-id analysis-001
```

`analyze` 重新核验固定产物和文件摘要，不访问模型服务、不需要现存权重、不重发推理、不改写来源报告。源产物不足时保留实际指标与复核原因，不能把历史执行失败改写成成功。

## 任务质量对齐的 NPU 测量

当比较阶段按 GPU 基线的 EvalScope 口径复测时，使用 `validator.py --action quality-run`。一次原生运行覆盖该 benchmark 的完整选定范围。读取同一份原生报告里的数据集和子集分数，并用原始文件的样本 ID 关联原生逐题结果。已有候选通过 `--action analyze` 携带 `source` 和 `expected` 复核。测量与只读复核分别写入独立尝试，分数差值和业务目标归比较任务处理。输入和证据格式见[测量与复核契约](references/quality-measurement.md)。

## 兼容与当前边界

旧 `validator.py` 参数及两个精度 shell 文件保留入口。完整请求之外的旧参数只作一致性约束，不能覆盖新请求。只有旧模型/端口参数时返回 `blocked` 和所缺交接，不启动一站式流程。部署、量化和性能参数须显式转交相应任务；旧 `report-path` 不用于覆盖原报告，实际报告由独立尝试目录交付。

内部解析模块包括原生结果、HTTP 证据及历史回执，运行边界内另核对原文件字段映射。完整目录和当前验收范围由调用方的分项材料给出。

直接精度请求省略版本时解析官方稳定版本，显式版本按原契约核验；客户首次运行和续接均使用准备阶段安装回执中的实际版本，历史分析也不查询 latest。保存实际版本、依赖和源码证据。使用同执行主机端点（运行时核验本机 IP 归属）、非流式且每样本一次生成。EvalScope 核对本地文件全量或前 N 行上限及标准文本 DataAdapter；AISBench 保留单个原文件切片的 reader 选择。其他选择布局、重复生成、流式精度、认证和外部裁判服务没有验收，不能静默转换。图像、音频、视频与性能能力不因存在历史脚本而成为本 Skill 的已验收能力。

显式 backend 优先；未声明 backend 且使用原有 `.py` 配置的请求按 AISBench 兼容识别，其他新请求默认 EvalScope。旧请求明确填写的 version 仍作为安装版本期望值核验，不查询最新；直接精度请求省略它即可跟随稳定发布；客户生成请求保持实际明确版本。工具不可用或配置不兼容时阻塞，不自动回退。历史分析依据来源工具及实际版本，不联网核验 latest。EvalScope 与 AISBench 的分数不能直接作为彼此的相对基线。新版原生格式仍须通过配置与证据核验，不能将尚未发布的版本表述为已经验收。

示例：[默认测量请求](assets/request.example.json)、[EvalScope 配置](assets/evalscope-config.example.json)、[AISBench 请求](assets/request.aisbench.example.json)、[AISBench 配置](assets/aisbench-config.example.py)、[分析请求](assets/analyze.example.json)。示例路径和摘要必须替换为实际资料，不能作为运行证据。
