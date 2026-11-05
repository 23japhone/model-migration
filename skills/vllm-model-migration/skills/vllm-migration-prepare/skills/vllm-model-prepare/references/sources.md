# 来源、SDK 与昇腾边界

核对日期：2026-10-13（Asia/Shanghai）。以下为一手资料与读取的源码快照，不表示这些 HEAD 或版本已在所有目标环境验收。

| 来源 | 采用内容 | 边界 |
| --- | --- | --- |
| [HF 下载指南](https://huggingface.co/docs/huggingface_hub/en/guides/download)与[API 文档](https://huggingface.co/docs/huggingface_hub/en/package_reference/file_download) | 显式版本、`local_dir`、官方缓存、文件过滤 | 获取 `model_info(files_metadata=True)`，用返回的 repo commit 下载；有 LFS SHA256 时比较 SHA256，否则比较 Git blob SHA1；不只信任目录名 |
| [ModelScope 模型下载官网](https://modelscope.cn/docs/models/download) | 官方下载入口 | 本次网页正文未能通过静态读取取得，接口细节以官方源码核对 |
| [ModelScope 下载源码](https://github.com/modelscope/modelscope/blob/958c3f7da68b700cc671134e267c356a2444398b/modelscope/hub/snapshot_download.py)与[官方 Hub SDK](https://github.com/modelscope/modelscope_hub/blob/d6f639c97f7afcde533d7036f715c52085258639/README.md) | `snapshot_download`、版本与缓存差异 | 不依赖隐式发布时间选版；旧 API 保留版本和逐文件信息，新桥接版本使用 Hub 公共 `list_repo_files/download_repo`。下载前后比较文件清单，核对大小与 SHA256；SDK 未返回逐文件 revision 时保留缺口，不伪装成 repo commit |
| [Safetensors 官方格式](https://github.com/huggingface/safetensors#format)与[头部资料](https://huggingface.co/docs/safetensors/metadata_parsing) | 无需加载权重的头部/偏移检查 | 脚本是资产结构检查，不替代模型加载或原始发布文件的完整性背书 |
| [vLLM-Ascend 0.18.0 安装](https://docs.vllm.ai/projects/ascend/en/v0.18.0/installation.html)与[版本矩阵](https://docs.vllm.ai/projects/ascend/en/v0.18.0/community/versioning_policy.html) | 后续运行环境必须使用配套 vLLM-Ascend | 本技能不安装 vLLM/CANN、不选通用 CUDA 镜像、不占用设备 |
| [vLLM Skills 源码](https://github.com/vllm-project/vllm-skills/blob/c99623410c1531148ff9b39fe1dcb27efbf4bf23/plugins/vllm-skills/skills/vllm-deploy-simple/scripts/quickstart.sh)与[Ascend 技能实例](https://github.com/Ascend/agent-skills/blob/155ac37bd169ddb89479af528297cfb2237400aa/skills/vllm-ascend-deploy/SKILL.md) | 独立脚本与模型来源信息的组织 | 不复制其安装、服务启动、cron 或资源清理流程；不按 NPU 卡数猜产品系列 |

## 依赖位置

官方 SDK 在执行下载的环境中安装；调用方环境和资产主机可以不同。查看实际 `huggingface_hub`、`modelscope` 版本，并留在来源记录。没有使用的 SDK 无需安装，本地核验可使用 `python3 -S` 运行。

本次核对了 ModelScope 1.36.3 的带 revision 参数 API 和目标侧 1.38.1/ModelScope Hub 0.1.8 的桥接 API。后者旧 `get_model_files` 丢失 revision/checksum，脚本因此选择公开 Hub API，不使用私有客户端；逐文件 revision 在该版本未暴露，使用 checksum 和文件集合锁定内容。其他版本需按实际 API 与结果验证，不承诺全版本兼容。

缺依赖时先报告缺口。在用户已授权下载环境准备的情况下，可在用户工作根的独立 venv 中安装对应客户端，核对实际版本后重试；不要修改系统 pip、全局镜像配置或已有服务容器。源码中的新旧 SDK 桥接变化不能视为所有版本兼容，目标侧以本次真实执行结果为准。

## 网络异常与代理

此规则同时适用于 Hugging Face 和 ModelScope。下载失败时先区分 SDK 依赖、仓库或 revision 不存在、访问权限、网络连接与文件身份问题。对网络错误核对 DNS、连接/TLS、当前执行进程的代理配置及代理可达性；仅记录必要的脱敏结果，不打印完整环境或凭据。一次超时、Network is unreachable 或 DNS 失败本身不足以证明代理配置错误。

确认当前网络需要代理，或现有代理不可达、认证失败、配置错误后，说明原因和受影响来源，先询问用户“是否有可用网络代理？”。用户已经明确回答时沿用该信息，不重复询问或将同一失败请求反复执行。

| 用户情况 | 后续操作 |
| --- | --- |
| 有可用代理 | 请用户提供代理地址、协议和端口，在实际下载主机、实际执行进程中使用；账号密码仅由用户保存在授权环境或配置中。取得可用配置后继续原模型准备任务 |
| 没有可用代理，受影响来源为 HF | 停止 HF 下载，按[无代理时改用 ModelScope](#无代理时改用-modelscope)准备对应模型；不要求用户必须新增 HF 代理 |
| 尚未回答 | 等待代理信息；本地核验和其他无依赖的任务继续 |

ModelScope 自身遇到已确认的代理问题时，同样先询问是否有可用代理；没有则记录该来源受阻，继续其他独立任务，不在两个来源之间循环重试。代理只是原模型准备任务的执行条件，不单独增加 HF 网络可达性验收任务。

通常通过官方 SDK 所用 HTTP 客户端的环境配置提供代理。示例只展示字段，地址、协议和端口由用户按实际代理填写：

```bash
export HTTPS_PROXY='http://用户提供的代理主机:端口'
export HTTP_PROXY="$HTTPS_PROXY"
# 然后在同一执行环境运行原来的 prepare.py 请求。
```

HTTP CONNECT 代理连接 HTTPS 目标时，HTTPS_PROXY 的值通常仍使用 `http://`，以实际代理协议为准。核对 NO_PROXY 不会使所需来源及文件重定向域名绕过代理；只在另一终端设置变量不会自动传给当前进程。具体语义参考 [HTTPX 环境变量](https://www.python-httpx.org/environment_variables/)与[Requests 代理配置](https://requests.readthedocs.io/en/latest/user/advanced/#proxies)，并核对实际 SDK 版本与客户端。代理地址或凭据属于执行环境，不写入模型请求、技能资源或交接日志；凭据由用户保存在授权配置或秘密管理渠道。

取得可用代理配置后，用原 repository、revision、资产目录和预期身份创建新的尝试，按原任务复核已有文件并补齐下载；代理已配置不等于资产已准备完成。保留原始失败清单和部分资产，原脚本网络失败仍为退出1、status=failed，不改写为资产通过。不自动修改全局网络配置或关闭 TLS 核验。

## 无代理时改用 ModelScope

HF 因已确认的代理问题不可用，且用户没有可用代理时，直接进入 ModelScope 来源准备，不再要求用户单独验收 HF 网络，也不重复请求换源许可。切换由调用方生成新请求并调用同一公开 CLI，脚本只执行请求明确指定的来源，不在失败请求内静默重试另一来源。

1. 核对 ModelScope 对应模型的发布者、模型与版本；不能只凭两个平台的仓库同名认定内容相同。没有明确对应模型时，请用户提供 ModelScope 官方链接，不猜测其他模型替代。
2. 新请求设为 `source=modelscope`，使用该平台实际的 repository 和 revision，保留原 task、modalities 与所需 scope；HF repo commit 不能直接作为 ModelScope revision。无法满足用户明确的版本或内容身份约束时，说明缺口，不静默降低约束。
3. 使用独立资产目录和新的尝试，保留 HF 失败清单、来源记录与部分文件。已核对为同一权重的 expected_files 可继续使用；expected_identity 必须对应新来源的实际文件清单，不能将 HF 整仓身份直接套用，也不能静默丢弃原有身份要求。
4. 按 ModelScope 原有 SDK、文件大小、摘要、tokenizer、processor、索引和分片规则核验。只交付本次实际来源及其版本与身份，不把 ModelScope 成功写成 HF 通过；所需 SDK 缺失时按[依赖位置](#依赖位置)处理。

## 开发示例

[Qwen3.5-27B 模型卡](https://huggingface.co/Qwen/Qwen3.5-27B)与[v0.18.0 固定源码教程](https://github.com/vllm-project/vllm-ascend/blob/e18643f8a4d5bd9990727654318ad069ea0b56e2/docs/source/tutorials/models/Qwen3.5-27B.md)用于开发资料核对。随附 Qwen 示例只提供显式请求样本，不设置通用模型、路径、TP 或硬件默认值；官网部署参数也不直接用于本任务的资产检查。
