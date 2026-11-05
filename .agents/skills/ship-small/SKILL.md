---
name: ship-small
description: Use when a verified vertical slice, bugfix, documentation change, or generated artifact has passed review and needs final scope checks, staging, commit, PR, or handoff preparation.
---

# Ship Small

## Overview

Use this skill to finish one small, verified slice without widening it. The goal is to turn accepted work into a clean handoff, commit, or PR with evidence, scope control, and explicit residual risks.

## Hard Rules

- Do not ship unverified work. If `verification-runner` has not passed or evidence is missing, route back to `verification-runner`.
- Do not ship over actionable review findings. If `bug-focused-reviewer` found issues, route to `debugging-loop` or `test-first-implementer` first.
- Do not add features, refactor, reformat, upgrade dependencies, or fix adjacent bugs during shipping.
- Do not stage unrelated files or user changes. Inspect `git status --short` and `git diff` before staging.
- Do not commit, push, or create a PR unless the user explicitly requested that action in the current task or prior approved plan.
- Do not hide skipped checks, generated files, secrets, large artifacts, or unresolved risks.

## Required Inputs

Prefer these inputs before acting:

- approved slice plan or task scope
- latest `verification-runner` report
- latest `bug-focused-reviewer` report
- `git status --short`
- current diff, changed files, generated files, and untracked files
- project final-response, commit, branch, and PR rules from `AGENTS.md` or equivalent

If scope, verification, or review evidence is missing, stop and recommend the missing upstream skill instead of guessing.

## Workflow

1. Load project rules.
   Read `AGENTS.md` and any final-response, safety, testing, commit, or PR rules it references.

2. Confirm the ship gate.
   Verify that the slice has passing evidence from `verification-runner` and no actionable findings from `bug-focused-reviewer`.

3. Inspect workspace state.
   Run `git status --short`. Inspect the relevant `git diff` and untracked files. Separate intended changes from unrelated user work.

4. Check scope.
   Compare the diff with the approved slice. Flag unrelated edits, dependency churn, generated artifacts, migrations, formatting-only changes, or docs changes that were not requested.

5. Check safety and hygiene.
   Look for secrets, credentials, private screenshots, large binaries, debug logs, local paths, temporary files, and accidental generated output. Do not stage suspicious files.

6. Decide the shipping action.
   - Handoff only: summarize what is ready and what the user should decide next.
   - Stage only: stage only intended files if the user requested staging.
   - Commit: create one focused commit if the user requested commit.
   - Push or PR: push/open PR only if explicitly requested and the repository tooling is available.

7. Prepare message text.
   For commits or PRs, write a short title and body from the verified slice: what changed, how verified, remaining risks, and any skipped checks.

8. Perform only approved actions.
   Run the smallest necessary git commands. After staging, commit, push, or PR creation succeeds, report the exact action and evidence.

## Subagent Use

Use subagents only when the user or prior plan explicitly asks, or when read-only checks are separable. Good read-only splits include diff scope, test evidence, security/secrets scan, and PR description drafting. The main agent owns staging, commit, push, PR creation, and final risk reporting.

Do not let subagents stage, commit, push, or create PRs unless the user explicitly delegated that action.

## Output Format

Use this report structure:

```text
What changed:
Ship gate:
Scope check:
Git action:
Verification evidence:
Skipped checks:
Remaining risks:
Recommended next step:
```

For a commit or PR message, use:

```text
Title:
<imperative, focused title>

Body:
- What changed:
- Verified:
- Risks or skipped checks:
```

## Routing

- Missing or stale verification: use `verification-runner`.
- Actionable review findings: use `debugging-loop` for diagnosis or `test-first-implementer` for a small scoped fix.
- Scope changed or the diff is too broad: use `vertical-slice-planner`.
- Ready to ship but user has not authorized git actions: provide a handoff summary and ask for the specific action.
- Ready and user requested git action: stage, commit, push, or open PR with only the intended files.

## Common Failure Modes

| Failure mode | Correction |
| --- | --- |
| Shipping because tests passed once | Require verification evidence and review status. |
| Staging all files | Stage only files proven to belong to the slice. |
| Fixing a bug while preparing commit | Route to `debugging-loop` or `test-first-implementer`. |
| Hiding skipped checks | Report skipped checks and why they were skipped. |
| Committing user work | Inspect status/diff and leave unrelated changes alone. |
| Creating a huge PR | Split or route back to `vertical-slice-planner`. |
| Vague commit message | Include behavior change and verification evidence. |
