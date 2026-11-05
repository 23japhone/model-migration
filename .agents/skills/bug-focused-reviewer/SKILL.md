---
name: bug-focused-reviewer
description: Use when reviewing a diff, completed slice, pull request, or generated change after verification and before fixing, committing, merging, or shipping.
---

# Bug Focused Reviewer

## Overview

Use this skill to review changes from a bug-first stance. Prioritize defects, regressions, missing tests, safety issues, and scope creep over summaries or praise.

## Hard Rules

- Do not edit files or fix issues unless the user explicitly asks.
- Findings come first, ordered by severity.
- Every finding needs concrete evidence: file path and line when available, failing behavior, violated acceptance criterion, or specific risk.
- Do not list style preferences as findings unless they can cause real maintenance or behavior risk.
- If no issues are found, say so clearly and list residual risks or skipped checks.

## Inputs To Prefer

Use the latest `verification-runner` report when available, plus:

- approved slice plan and acceptance criteria
- current diff and changed files
- test and manual verification results
- project rules from `AGENTS.md` or equivalent
- relevant docs, interfaces, schemas, or public APIs touched by the diff

If verification has not run, say review confidence is lower and recommend `verification-runner` before shipping.

## Review Workflow

1. Read project rules and the approved slice scope.
2. Inspect `git status --short` and the current diff.
3. Compare the diff against acceptance criteria, non-goals, and public contracts.
4. Look first for behavior bugs, regressions, data loss, security/privacy issues, missing tests, and bad failure modes.
5. Check whether tests prove the changed behavior, not merely implementation details.
6. Check scope control: unrelated refactors, dependency changes, generated files, migrations, or formatting churn.
7. Report findings first. Keep summaries brief and secondary.

## Severity Guide

- `P0`: breaks core flow, data loss, security leak, destructive operation, or cannot ship.
- `P1`: likely user-visible bug, regression, broken contract, or important missing test.
- `P2`: edge-case bug, maintainability risk with concrete impact, weak verification.
- `P3`: minor risk or cleanup that is worth considering but not blocking.

## Output Format

Return:

```text
Findings:
Open questions:
Residual risks:
Change summary:
Recommended next step:
```

Finding format:

```text
[P1] Short title
Evidence: file:line or concrete diff/test/behavior evidence
Impact: what can go wrong
Recommendation: smallest fix or next diagnostic
```

If there are no findings:

```text
Findings:
No blocking or actionable issues found.
Residual risks:
...
Recommended next step:
Use $ship-small if the user wants to prepare the slice for commit.
```

## Subagent Handling

Use subagents only when the user or prior plan explicitly asks, or when independent read-only review passes can run safely in parallel. Good splits include tests, security/privacy, API contracts, and UI/accessibility. Review subagents must not edit files. The main agent owns final severity and deduplication.

## Next Step Routing

- Use `debugging-loop` when a finding has a reproducible failure or root-cause work.
- Use `test-first-implementer` when a finding requires a small code fix within the approved slice.
- Use `vertical-slice-planner` when the fix changes scope or acceptance criteria.
- Use `ship-small` when no actionable findings remain and verification is acceptable.

## Common Mistakes

- Starting with a change summary instead of findings.
- Treating formatting or taste as a bug.
- Reporting vague concerns without line-level or behavior evidence.
- Ignoring missing tests because the current happy path works.
- Reviewing only changed lines while missing broken contracts at boundaries.
- Fixing issues during review and mixing implementation with review judgment.
