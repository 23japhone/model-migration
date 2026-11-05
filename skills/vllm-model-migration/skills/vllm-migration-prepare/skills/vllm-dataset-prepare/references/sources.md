# 配方来源与格式依据

核对日期：2026-10-14。提取既有 GSM8K、C-Eval、MMLU 下载配方，保留原发布包和目录布局；将原 HTTP 下载地址改用已实际核对的同源 HTTPS 地址。没有复制安装、服务探测或评分步骤。

| 配方 | 发布来源 | 本次资料边界 |
| --- | --- | --- |
| GSM8K | [OpenCompass 发布包](https://opencompass.oss-cn-shanghai.aliyuncs.com/datasets/data/gsm8k.zip)、[原始研究数据仓库](https://github.com/openai/grade-school-math/tree/3101c7d5072418e28b9008a6636bde82a006892c) | 本次包3,409,276字节；train/test JSONL；不改原始 question/answer 或答案分隔标记 |
| C-Eval | [ModelScope 发布包](https://www.modelscope.cn/datasets/opencompass/ceval-exam/resolve/master/ceval-exam.zip)、[官方数据主页](https://modelscope.cn/datasets/opencompass/ceval-exam) | 本次包1,548,171字节；保留 dev/val/test 表头 CSV；master 为可变分支，不能称为已固定 repo commit |
| MMLU | [OpenCompass 发布包](https://opencompass.oss-cn-shanghai.aliyuncs.com/datasets/data/mmlu.zip)、[原始研究仓库](https://github.com/hendrycks/test/tree/4450500f923c49f1fb1dd3d99108a0bd9717b660) | 本次包2,471,795字节；保持 dev/test 无表头六列格式，不自行换答案编码 |

配方字段集中维护在 [recipes.json](recipes.json)。版本或格式变化时以真实文件和本次来源证据复核，示例不是跨版本保证。

加载器依据为 AISBench 源码快照 `5a63d936e40dd04addfcffb2401f84cd0e53bb46`：

- [GSM8K 加载器](https://github.com/AISBench/benchmark/blob/5a63d936e40dd04addfcffb2401f84cd0e53bb46/ais_bench/benchmark/datasets/gsm8k.py)读取 train/test，并使用原始答案分隔格式。
- [C-Eval 加载器](https://github.com/AISBench/benchmark/blob/5a63d936e40dd04addfcffb2401f84cd0e53bb46/ais_bench/benchmark/datasets/ceval.py)读取科目 dev/val/test，不将 test 的缺失公开答案改成其他标签。
- [MMLU 加载器](https://github.com/AISBench/benchmark/blob/5a63d936e40dd04addfcffb2401f84cd0e53bb46/ais_bench/benchmark/datasets/mmlu.py)要求无表头六列 dev/test 文件。

这些是资产布局和基本格式依据，不执行或复制评分器逻辑。不在本技能中安装 datasets、AISBench 或推理框架，不运行精度评测。源码读取不等于当前评测器运行通过。

## 通用来源与完整性

通用来源支持 HTTPS 文件/ZIP/tar 和本地 JSONL、JSON对象列表、CSV。保留原题与原文件字节；未知 Parquet、音视频或其他专用格式明确不支持，使用专门配方后再扩展，不在这里猜测。

HTTPS、Content-Length 和 ETag 不替代发布者的不可变版本证明。记录原始 payload SHA256、解包文件逐项摘要和内容身份；可通过 expected_sha256 固定已有发布快照。缓存复用比较来源 URL、revision 声明、payload、解包目录及文件摘要，不静默覆盖源冲突。原始来源记录和运行证据保存在技能包外。

## 网络异常与代理

此规则适用于所有 HTTPS 来源，包括 Hugging Face 或 ModelScope 的数据链接；本技能直接读取 HTTPS 文件，不调用它们的下载 SDK。先区分输入、权限、来源/内容冲突与网络错误，按需要核对 DNS、连接/TLS、实际执行进程的代理配置和代理可达性。一次超时、Network is unreachable 或 DNS 失败本身不足以证明代理错误。

确认当前网络必须经代理访问但未配置，或代理不可达、认证失败、配置错误时，明确提示用户或管理员在实际下载主机、执行进程中配置或修正网络代理。没有可用代理则等待条件，不重复同一失败请求；其他独立来源或本地资产核验可以继续。代理凭据仅保存在授权环境/配置中，不写入请求、技能资源或日志，不自动修改全局网络配置或关闭 TLS 核验。

下载器使用 Python 标准库的[环境代理处理](https://docs.python.org/3/library/urllib.request.html#urllib.request.ProxyHandler)。示例仅含占位符，地址、端口和协议由用户按实际代理填写：

```bash
export HTTPS_PROXY='http://用户提供的代理主机:端口'
export HTTP_PROXY="$HTTPS_PROXY"
# 在同一环境运行原来的数据准备请求。
```

HTTP CONNECT 代理访问 HTTPS 目标时，HTTPS_PROXY 的值通常仍使用 http://；以实际代理协议为准。NO_PROXY 不应将所需来源及文件重定向域名排除在代理之外。在另一终端设置变量不会自动传给当前执行进程；具体配置按实际操作系统与下载进程核对。

配置后核对来源与实际下载路径可达，以原 URL、revision 声明、目录及预期摘要新建尝试，复核已有文件、补齐下载并验收复用。保留旧 failed 清单与部分资料，不将说明更新或代理已配置当作准备已完成。每一跳重定向在发送请求前检查 HTTPS；普通 HTTPS 跳转仍按标准处理，不更换用户指定来源绕过错误。
