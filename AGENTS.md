# Codex 项目规则

本仓维护面向 vLLM / vLLM-Ascend 的模型部署迁移适配与精度测试 Skills。交付形态是总入口、三个阶段、八个任务组成的 12 个嵌套 Skill；不依赖 MoFix 后端，不建设 Agent 宿主插件、HTTP 服务或后台调度系统。普通说明使用中文，技术标识保持原名。

## 开始工作

1. 先读本文件与 [项目简介](docs/PROJECT_BRIEF.md)，检查 `git status --short --branch`、当前分支及适用的局部规则。
2. 需求范围以 [功能规划汇总](docs/MODEL_MIGRATION_FEATURE_PLAN_SUMMARY.md) 为依据；组织、调用和当前限制见 [架构](docs/ARCHITECTURE.md)。
3. 进入某个业务前，完整阅读该 Skill 的 `SKILL.md`、公开契约和相关源码；从 [接口索引](docs/INTERFACES.md) 定位入口。跨模块改动读取所有受影响模块。
4. 开发前读 [工作流程](docs/WORKFLOWS.md)、[代码质量](docs/CODE_QUALITY.md) 和 [验证](docs/TESTING.md)；涉及外部操作时读 [安全边界](docs/SAFETY_POLICY.md)。
5. 文档描述、源码行为与运行证据冲突时，明确记录差异。用户当前要求决定授权和范围，源码说明当前实现，运行结论必须绑定实际版本和本次证据；不得把规划或历史实测写成当前通过。

## Git 硬约束

- 新功能必须从**当前本地 `dev`** 创建 `codex/<任务名>` 分支；修复和文档默认相同。不得直接在 `dev` 或 `main` 开发，不以 `origin/dev` 或 `main` 替代本地 `dev` 起点。
- 先完成改动、验证和审查，展示具体差异，取得用户的 **commit 授权**后才提交。提交后展示提交 ID、合入范围和验证结果，另取**合入 `dev` 授权**；默认本地 `--no-ff` 合并。开发授权不包含这两次授权，合并授权包含该次合并提交。
- **Git 提交信息必须使用中文。** 普通提交和合并提交的标题、说明正文均使用中文；代码标识、文件路径、命令和必要的英文技术名词保留原文。不得直接采用 Git 默认生成的英文合并消息。
- 默认不 push；本仓工作流不创建远程 PR。所有开发合入在本地完成。
- **Codex 不修改 `main` 引用。** 不在 `main` 提交，不向其 merge、rebase、cherry-pick、reset，不用 `branch -f`、`update-ref`、特殊 fetch refspec 或其他方式移动它，也不代用户同步。
- `main` 必须与 `origin/main` 指向同一提交；使用 `git rev-parse main origin/main` 检查，并用 `git ls-remote --heads origin main` 区分当前远端与缓存引用。发现差异交由用户手动处理；无法查询时标记“远端未核实”，不声称一致。可以继续不依赖该差异的 `dev` 工作，但必须保留异常说明。
- 创建工作分支、提交及合入前后记录并核对 `main` 提交 ID。不得为了恢复一致擅自重置任何分支。
- 保留用户改动；不自动 stash、清理、删除分支或重写历史。工作区不干净时先辨认归属，使用不会覆盖用户内容的工作方式。
- 汇报 Git 状态时区分未提交工作区修改、分支提交、已合入与已推送。未提交修改不能称为已保存在某分支的提交中；切换分支时若携带这些修改，明确说明其仍在当前工作区，并核对内容未丢失。

### .gitignore 维护

- [.gitignore](.gitignore) 是应纳入版本管理的仓库配置，具体忽略模式只在该文件维护，不复制到规则文档。
- 本仓新增的专属忽略规则统一写在 `# local code repository` 下方；保留上方已有模板，不顺带改写。
- 忽略缓存、临时文件和个人状态，不得误忽略源码、契约、文档及共享质量配置。当前 `.codex/code-structure-policy.toml` 和 `.codex/code-structure-baseline.json` 应保留，不整体忽略 `.codex/`。
- 修改后用 `git check-ignore --no-index -v` 核对应忽略与应保留的代表路径。Git 忽略规则与源码质量审计的排除规则分别维护，前者不替代或修改后者。

## 开发约束与技能路由

- 所有代码变更使用 `maintainable-code-guardrails`；新增职责、模块、入口、共享、依赖或公共边界变化，以及热点变更，另用 `codebase-organization-guardrails`。唯一质量规则在 [CODE_QUALITY](docs/CODE_QUALITY.md)，先记录 Change Shape Map 和修改前指标，再改动。
- 多分支错误处理使用 `contain-workflow-failures`，先记录失败边界；多步骤执行使用 `preserve-task-continuity`，维护任务台账。独立步骤失败不能吞掉其他就绪工作，最终失败也不能被部分成功掩盖。
- 行为变更先明确验收，再用 `test-first-implementer`；故障先用 `debugging-loop` 定位。完成后用 `verification-runner` 验证、`bug-focused-reviewer` 审查，最后用 `ship-small` 准备授权交接。这些技能不授予提交、合并或外部运行权限。
- 文档、规则与交接知识收尾使用 `neat-freak`，按 [知识收尾约定](docs/WORKFLOWS.md#知识收尾)核对现役事实、权限及清场边界。
- 工作流技能使用本仓 [`.agents/skills/`](.agents/skills/) 下的完整副本，入口和依赖见 [开发技能索引](docs/DEVELOPMENT_SKILLS.md)。使用时明确读取仓库内对应 `SKILL.md`；个人环境存在同名技能时，也不依赖自动合并或个人副本。缺失时报告仓库资源不完整，并继续不依赖它的检查，不隐式安装。
- 以后本仓规则、计划或任务新采用工作流技能，或已有技能引用其他技能时，必须在同一变更中将新增技能及其所需技能依赖、脚本、参考资料、元数据和许可文件完整纳入 `.agents/skills/`。同步路由、索引及来源清单，按 [新增技能纳入流程](docs/DEVELOPMENT_SKILLS.md#新增工作流技能的纳入流程)验证后再交付；不能仅写技能名或依赖个人安装。当前技能清单不是数量上限，与本仓工作流无关的个人技能不批量导入。
- 保留三层职责；跨 Skill 使用公开 CLI、文件契约及固定引用，不导入兄弟 Skill 私有实现。共享路径能力与 Schema 唯一归属见架构；不新建同义入口或无归属的 `utils/common/helpers` 包。
- Agent 负责需求理解、源码适配和语义复核；脚本负责确定性执行和证据。`verify-agent` 唯一管理模型服务，精度任务只管理自有评测进程；borrowed 服务不停止。
- vLLM 模型注册的 `mode=plugin` 是业务能力，不等于 Agent 宿主插件，不能按名称删除。
- 默认 EvalScope，兼容显式 AISBench、旧配置和显式版本；历史 `analyze` 不联网核验 latest、不新增推理或评分。示例值不得成为本次默认模型、设备、负载或阈值。
- 执行完成、功能通过、测量有效、GPU/NPU 可比、目标达标分别判断。完整流程还需三个 JSON 的独立导出回执；交接成功不代表性能优化完成。
- 运行资产、请求和证据放在明确的技能资源外位置。外部操作依照本次授权；客户 EvalScope 每次新任务或续接准备均调用原版安装脚本，默认共享 /data/evalscope-venv，可由内部 context 传安装选项；AISBench 与资产 SDK 保留任务隔离流程，详见[安装契约](skills/vllm-model-migration/references/evalscope-installation.md)。代码开发授权不包含真实安装或设备运行授权；不擅自占用设备、重试、回滚或清理他人资源。凭据不得复制到新文档、请求、日志、报告或记忆。

## 验证与交付

在仓库根执行 `git diff --check`，并按 [TESTING](docs/TESTING.md) 运行与改动相关的静态检查、公开入口检查、文档检查及结构审计。结构审计命令：

```bash
python3 -B .agents/skills/codebase-organization-guardrails/scripts/source_shape_audit.py \
  --repo-root . --policy .codex/code-structure-policy.toml \
  --baseline .codex/code-structure-baseline.json
```

不为文档修改运行模型、下载资产或安装依赖。当前入口检查结果与未验证范围见验证文档，必须区分既有失败与本次回归；零测试不算测试通过。软阈值提示要解释，硬阈值或基线退化不得靠刷新基线掩盖。

交付时说明改动及原因、实际命令和结果、审查发现、失败/阻塞/未验证范围、资源状态（如涉及运行）、当前分支和未执行的 Git 操作。先交付可审查结果，再请求 commit；合入 `dev` 单独确认。路线图不是自动实施授权。

本规则约束 Codex 行为，不声称仓库已安装 Git hooks、CI 或技术性分支保护。
