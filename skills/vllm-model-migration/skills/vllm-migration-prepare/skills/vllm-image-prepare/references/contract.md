# 镜像请求与交接契约

所有路径属于执行主机；相对 archive 路径按 work-root 解析。凭据不能放进请求；Docker 使用执行环境既有认证，不调用 login，不更改 daemon 配置。未知字段拒绝。

| 字段 | 约束及用途 |
| --- | --- |
| `source` | `reuse`、`registry` 或 `tar` |
| `reference` | 复用/registry 必填；显式 tag、digest 或完整 image ID；不能隐式使用 latest，显式 latest 也必须用预期身份固定。registry 必须为 repository 引用；tar 若提供引用，须等于预期 ID 或归档已有标签 |
| `expected_image_id` | 完整 `sha256:` 加 64 位小写摘要；tar 必填 |
| `expected_digest` | repository digest，格式同上；registry 必填。包含 digest 的 reference 必须与其相同 |
| `expected_index_digest` | registry 可选。多架构标签的索引摘要；`expected_digest` 仍固定目标平台的子 manifest。客户缺省流程从同一次 Quay 镜像发现取得两者 |
| `archive` | tar 的 Docker save 文件路径；不支持 docker export 或任意 OCI 归档 |
| `archive_sha256` | tar 可选的 64 位小写文件摘要；与 image ID 分别检查 |
| `platform` | Linux 目标平台，支持 arm64、amd64 及显式 variant；arm64 与 arm64/v8 可对应；同时核对所连 daemon 架构 |
| `hardware` | `name` 芯片名称及 `family` 产品系列；来自目标实际资料，脚本不自动探测 NPU |
| `recipe` | 必须包含 `source_ref`、`vllm`、`vllm_ascend`、`hardware_families`、`evidence`；上游 repository 为 vLLM-Ascend 镜像，两个版本必须明确；目标系列须在适用列表内；资料为 HTTPS 链接。脚本只检查结构与条件，不浏览资料或验证安装依赖 |
| `timeout_seconds` | Docker 单个命令等待时间，1—3600 秒，默认 600；超时后 daemon 状态需要复核 |

至少提供一种预期镜像身份。已有引用存在但身份或平台冲突，直接失败，不用 pull 覆盖它。registry 的 tag 缺失时继续按固定的平台 digest 和可选索引 digest 查本地缓存，确认各引用均缺失才按平台 digest 拉取。输出记录实际 ID 和 RepoDigests；按 digest 拉取可以只留下 digest 引用。Docker daemon 使用环境既有 context，实际名称和 ID 记录在清单中，不能将本地 CLI 所在主机当成 daemon 主机。

按多架构 tag 提前拉取时，本地 RepoDigests 可能只记录索引摘要。若该摘要与 `expected_index_digest` 匹配，镜像任务读取固定索引的 manifest，核对目标平台唯一子 manifest 的 digest，以及其 config digest 与本地 image ID。核对通过后直接复用，在资产回执保留映射证据和 Docker 实际返回的 RepoDigests；索引、平台或内容冲突时停止。

## 归档检查

脚本检查外层成员相对路径、重复项、符号链接和特殊文件；读取 manifest.json 和 config JSON，按原始 config 内容计算 image ID；核对每层未压缩内容与 rootfs.diff_ids；流式计算归档 SHA256；检查平台和所有将恢复的 RepoTags。多镜像归档整体拒绝，要求调用者重新导出一份镜像，避免隐式加载其他资产。

相同 ID 已存在时仍检查归档、身份及标签冲突，随后复用，不恢复缺失的 tag。需创建额外 tag 时由调用者另行明确处理。tar 恢复的上游 RepoDigests 可能为空，source 保留待复核；归档摘要和预期 ID 只证明内容身份，不证明发布者或 vLLM-Ascend 安装完整性。

检查与 Docker load 之间没有 daemon 原子锁；调用者需保证稳定的镜像变更窗口。CLI 会复查实际 ID 和已复用引用的身份，不能保证其他并发写入者不改 tag，也不自动回滚或删除镜像。

## 输出及退出码

每次实际执行保存 `<work-root>/<run-id>/vllm-image-prepare/<独立尝试>/image-manifest.json` 与 `docker-commands.json`。清单沿用 schema_version、run_id、task_id、inputs、status、conclusions、evidence、uncovered；附实际 asset、daemon、hardware、recipe、开始结束时间和诊断。命令证据只含允许的 argv、返回码和脱敏 stderr，不保存 inspect 的环境变量或完整 daemon 配置。

- `0`：计划有效，或请求范围资产通过；仍需查看来源、硬件待复核和运行未评估结论。
- `1`：Docker、身份、平台、归档或证据执行失败，保留 failed 清单；磁盘不可写时无法保证清单落盘。
- `2`：输入或证据路径无效，执行前拒绝，不创建运行文件。
- `3`：缺少 Docker CLI，保留 blocked 清单；不隐式安装。

证据根必须位于完整总 Skill 根之外，不能通过运行路径符号链接重定向；保护范围不依赖插件标记。计划模式只核验输入和输出位置，没有联网、Docker 或运行文件写入。
