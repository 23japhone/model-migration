---
name: verification-runner
description: Use when verifying a completed implementation, vertical slice, bugfix, or generated artifact before review, commit, or further feature work.
---

# Verification Runner

## Overview

Use this skill to independently verify that the latest change satisfies its acceptance criteria without adding new functionality.

## Hard Rules

- Do not add features, broaden scope, refactor unrelated code, or silently fix failures.
- Do not claim success without exact commands, outcomes, or manual evidence.
- Do not treat a narrow passing check as proof of broad completion.
- If verification fails, explain the failure first and recommend the smallest next diagnostic or fix path.
- Preserve user changes and inspect `git status --short` before drawing conclusions.

## Required Inputs

Prefer the output from `test-first-implementer`, including:

- what changed
- RED check and GREEN check
- broader verification already run
- skipped checks
- remaining risks
- approved slice acceptance criteria

If these are missing, reconstruct them from project docs, recent diff, and test configuration before running commands.

## Workflow

1. Load rules: read `AGENTS.md`, relevant docs, the approved slice plan, and implementation summary.
2. Inspect state: run `git status --short` and identify changed files.
3. Map acceptance criteria to evidence. For each criterion, name the command, test, manual check, screenshot, log, or diff evidence that can prove it.
4. Re-run the smallest relevant targeted checks from the implementation.
5. Run the smallest broader checks required by project docs, such as lint, typecheck, unit tests, smoke tests, docs checks, or build.
6. Perform manual verification when the slice requires UI, CLI, device, file, document, model, or external-service behavior that tests do not cover.
7. Inspect skipped checks and decide whether they are acceptable risks.
8. Report pass/fail per acceptance criterion and recommend the next skill.

## Failure Handling

When a check fails:

- keep the failing command and output summary
- identify whether failure is from the new change, environment, missing dependency, bad test, or unrelated existing issue
- do not implement a fix unless the user explicitly asked verification to include fixing
- recommend `debugging-loop` for reproducible failures
- recommend returning to `vertical-slice-planner` when acceptance criteria or scope are unclear

## Subagent Handling

Use subagents only when the user or prior plan explicitly asks, or when independent read-only verification can run safely in parallel. Verification subagents should be read-only unless explicitly assigned a fix. The main agent owns the final pass/fail judgment.

## Output Report

Finish with:

```text
Verification result:
Commands run:
Manual checks:
Acceptance criteria status:
Skipped checks:
Failures or anomalies:
Remaining risks:
Recommended next step:
```

Use `bug-focused-reviewer` next when verification passes and there is a diff. Use `debugging-loop` next when verification fails with a reproducible error.

## Common Mistakes

- Running only the test that was already green and calling the slice complete.
- Forgetting manual checks for UI, CLI, device, generated files, or external behavior.
- Fixing code while supposedly verifying.
- Ignoring skipped checks from the implementer.
- Hiding flaky, environment-dependent, or unrelated failures instead of labeling them.
- Reporting "all good" without exact command output or acceptance-criteria mapping.
