# 团队开发工作流技能

本仓将开发规则使用的技能及其支持资源保存在 `.agents/skills/`，随仓库版本管理。该目录服务于代码开发、规划、排错、审查和交付；业务交付的 12 个模型迁移 Skill 仍位于 `skills/vllm-model-migration/`。

## 官方目录与团队使用

2026-10-27 核对 [Codex 官方技能文档](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)：Codex 的仓库级扫描范围是当前目录向上到仓库根的 `.agents/skills/`，每个技能有自己的 `SKILL.md`。官方位置分为：

| 范围 | 位置 | 本仓使用方式 |
| --- | --- | --- |
| 仓库 | `$CWD/.agents/skills`、向上的同名目录，直到 `$REPO_ROOT/.agents/skills` | 本仓统一放根目录，供团队共享 |
| 用户 | `$HOME/.agents/skills` | 可有个人技能，本仓工作流不依赖它 |
| 管理员 | `/etc/codex/skills` | 管理员提供的能力，不是本仓分发位置 |
| 系统 | Codex 随附位置 | 官方随附能力；被本仓声明为工作流依赖时，也按下方纳入流程处理 |

团队成员取得完整仓库后，可从仓库根或子目录启动 Codex，使用下表技能。首次验证时检查宿主技能列表是否出现本仓路径；未显示时按官方说明重启并检查版本及实际启动位置。文件和脚本校验通过不替代宿主发现验收。

同名技能可能同时出现在选择器，Codex 不会合并其内容。执行本仓规则时明确读取这里的副本，审计命令始终使用相对仓库路径。其他 Agent 可显式读取同一份资源，其自动发现方式需按宿主另验；这里不推断 Claude Code 会自动扫描本目录。

## 技能与依赖闭包

前十项由根规则直接路由，后两项由这些技能的规划与上下文流程引用。每个目录提供完整执行资源，包括 `SKILL.md`、已有 `agents/openai.yaml`、支持脚本、参考文档及许可/归属文件，不使用指向个人电脑的符号链接。

| 技能入口 | 用途 | 本仓内其他技能引用 |
| --- | --- | --- |
| [maintainable-code-guardrails](../.agents/skills/maintainable-code-guardrails/SKILL.md) | 修改代码的可维护性约束 | codebase-organization-guardrails、test-first-implementer、verification-runner |
| [codebase-organization-guardrails](../.agents/skills/codebase-organization-guardrails/SKILL.md) | 源码放置、边界与结构审计 | 无额外技能依赖，携带自身脚本与参考文档 |
| [contain-workflow-failures](../.agents/skills/contain-workflow-failures/SKILL.md) | 流程失败隔离 | 无 |
| [preserve-task-continuity](../.agents/skills/preserve-task-continuity/SKILL.md) | 多步骤任务连续性 | contain-workflow-failures |
| [test-first-implementer](../.agents/skills/test-first-implementer/SKILL.md) | 已明确范围的验证优先实现 | vertical-slice-planner |
| [debugging-loop](../.agents/skills/debugging-loop/SKILL.md) | 复现与定位故障 | repo-context-loader、verification-runner、bug-focused-reviewer、test-first-implementer、vertical-slice-planner |
| [verification-runner](../.agents/skills/verification-runner/SKILL.md) | 验收与验证报告 | debugging-loop、bug-focused-reviewer、test-first-implementer、vertical-slice-planner |
| [bug-focused-reviewer](../.agents/skills/bug-focused-reviewer/SKILL.md) | 验证后的问题审查 | debugging-loop、ship-small、test-first-implementer、verification-runner、vertical-slice-planner |
| [ship-small](../.agents/skills/ship-small/SKILL.md) | 已验收变更的交付准备 | bug-focused-reviewer、debugging-loop、test-first-implementer、verification-runner、vertical-slice-planner |
| [neat-freak](../.agents/skills/neat-freak/SKILL.md) | 文档、规则、获准记忆与工作区知识收尾 | 无额外技能依赖；携带只读盘点脚本和四份参考资料 |
| [vertical-slice-planner](../.agents/skills/vertical-slice-planner/SKILL.md) | 定义最小可验证切片 | repo-context-loader |
| [repo-context-loader](../.agents/skills/repo-context-loader/SKILL.md) | 只读仓库上下文加载 | 无 |

表中的引用包括按条件进入的下一步骤，不意味着一次任务须执行全部技能，也不构成程序导入环。技能不能扩大授权，commit 与本地合入 dev 仍须分别确认。

## 来源与维护

[skill-sources.json](../.agents/skill-sources.json)记录 2026-10-27 用户提供的本地技能快照、直接/间接技能集合及 37 个资源的路径、大小、权限和 SHA256。它不包含自身摘要，不宣称这些自定义技能是 OpenAI 官方内置技能或经过远端 revision 验证。原有 LICENSE 和 ATTRIBUTION.md 原样保留。

`neat-freak` 3.0.0 纳入全部六个执行资源。作者的 `evals/` 是技能编写评测与模拟项目夹具，不是执行依赖，本仓不分发；来源清单明确记录此范围。仓库副本的平台速查按官方资料修正 Codex 技能目录和 Claude 的 AGENTS.md 支持，清场参考将部署要求限定于适用项目；兼容性说明移入 metadata，以通过当前 Codex 技能校验器且保留原文。清单保留上游原始摘要及本地修订说明。其余已纳入技能的原始快照不变，个人来源目录不自动回写。

更新技能时明确来源和影响范围，检查技能引用闭包、相对链接、元数据及脚本，保留许可资料，同步来源清单和受影响文档。审计脚本也是受审源码，更新后按 [代码质量](CODE_QUALITY.md)处理结构基线；不得从个人目录自动覆盖仓库版本。

## 新增工作流技能的纳入流程

本约定持续适用于本仓规则、实施计划或实际开发任务新采用的工作流技能，以及现有技能新增的其他技能引用。上面的 12 个技能是当前快照，不是固定上限；本机新安装但与本仓工作流无关的技能不自动进入仓库。

1. **确认用途与来源。** 先检查仓库是否已有同等能力，说明新增技能的消费者、触发条件和来源；核对可复制范围与许可。本地已有且允许复制的资源可在当前任务范围内直接纳入，不因普通文件复制增加审批步骤；Git 提交、合入及外部操作仍沿用原授权边界。
2. **纳入完整资源与技能依赖。** 放入 `.agents/skills/<skill-name>/`，包含 `SKILL.md` 及所需脚本、参考资料、模板、元数据、许可和归属文件；递归核对其引用的其他工作流技能，在仓库内形成完整依赖闭包。条件路由也要有可用的技能入口，但不意味着每次任务都执行全部技能。不得用个人目录绝对路径或符号链接代替仓库副本，也不复制缓存、凭据和个人运行状态。
3. **同步规则和来源记录。** 在同一变更中更新根规则的适用路由、本页技能/依赖表，以及 `.agents/skill-sources.json` 的技能集合、依赖关系、来源说明和资源摘要。仅被其他技能引用的条目可登记为间接依赖，无需强制新增顶层路由；不能只在文档写一个尚未纳入的技能名。
4. **验证团队可用性。** 按 [TESTING.md](TESTING.md#开发技能资源检查)核对资源集合、名称、元数据、相对链接、依赖与 SHA256，确认必需文件未被 `.gitignore` 隐藏。所带脚本要完成适用的语法及行为检查，命令使用仓库路径；新增或改变脚本依赖时，验证可复现的环境条件，不依赖作者个人技能目录。未做的宿主发现或其他机器验证明确列出，不用离线检查替代。
5. **处理源码增长并交付。** 新增或修改支持脚本时执行源码组织与结构审计，按现行规则更新基线，不能上调阈值掩盖热点。交付说明新增技能、依赖、来源、实际验证和未覆盖项；文件完整且检查通过后才把对应工作流声明为已纳入。

不分发的作者评测夹具必须确认不被执行入口、参考资料或脚本引用，并在来源清单记录范围与理由；不能借此省略任何实际依赖。仓库适配过的资源同时记录上游摘要、现役摘要和修订原因，后续更新不再按“与来源逐字一致”验收。

来源不可取得、许可范围不明或依赖不能随仓库提供时，明确报告缺口，阻塞依赖该技能的步骤并继续独立工作；不得默默退回个人安装、伪造空技能或宣称团队工作流已完整交付。已明确指定为本仓工作流依赖的技能不因位于用户、管理员或系统目录而免除可复现要求；确实无法提供项目副本时，记录该限制与明确的环境前置条件，不能把它写成无个人依赖的仓库内技能。

## 克隆后的本地验证

从仓库根运行，控制解释器为 Python 3.10+：

```bash
python3 -B .agents/skills/codebase-organization-guardrails/scripts/source_shape_audit.py \
  --repo-root . --policy .codex/code-structure-policy.toml \
  --baseline .codex/code-structure-baseline.json
```

该命令只使用 Python 标准库及本仓资源，不要求作者的全局技能目录。完整资源检查、文档检查及当前软提示见 [验证文档](TESTING.md)。本轮完成文件、依赖和脚本的离线验证；尚未在同事电脑或新启动的 Codex 会话中观察实际发现和选用。
