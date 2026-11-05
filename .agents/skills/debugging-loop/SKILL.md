---
name: debugging-loop
description: Use when a test, verification command, CI check, runtime behavior, review finding, or user-reported workflow fails and the root cause must be diagnosed before fixing.
---

# Debugging Loop

## Overview

Use this skill to turn a failure into a verified fix. The loop is: reproduce the failure, isolate the root cause, prove the bug with a regression check, make the smallest fix, then verify the original failure and nearby risks.

## Hard Rules

- Do not guess, rewrite broadly, or "clean up while here" before the root cause is known.
- Do not fix before reproducing the failure or inspecting enough raw evidence to explain why it happens.
- Do not change tests to match the implementation unless the test is proven wrong by the acceptance criteria or documented contract.
- Do not treat "I think this should fix it" as evidence. A rerun command, log, screenshot, or manual trace must confirm it.
- Do not broaden scope. If the fix reveals a larger feature or design change, stop and route back to `vertical-slice-planner`.
- Preserve user work. Run `git status --short` before edits and do not revert unrelated changes.

## Inputs

Start from the most concrete evidence available:

- Failed command and exact output from `verification-runner`, CI, or local tests.
- Finding from `bug-focused-reviewer`, including file and line references.
- User repro steps, expected behavior, actual behavior, screenshots, logs, or affected data.
- Approved slice plan, acceptance criteria, non-goals, and relevant project rules.

If evidence is missing, reconstruct it from the repo when practical. If the failure cannot be reproduced and no durable evidence exists, report what was tried and ask for the smallest missing artifact, such as command output, repro steps, input data, or a screenshot.

## Workflow

1. Load the local rules and recent context.
   Read `AGENTS.md` and relevant docs named by it. If the user provided a `repo-context-loader`, `verification-runner`, or `bug-focused-reviewer` output, use that as the starting map.

2. Inspect workspace state.
   Run `git status --short`. Identify user changes, generated files, and files likely touched by the failing slice.

3. State the debugging frame.
   Write down:
   - expected behavior
   - actual behavior
   - exact failing command or manual repro path
   - first file or subsystem to inspect

4. Reproduce or preserve evidence.
   Rerun the narrowest failing command or follow the manual steps. Capture the important lines of output. For flaky failures, rerun enough times to identify whether it is deterministic, timing-sensitive, or environment-sensitive.

5. Isolate the root cause.
   Trace from symptom to boundary to implementation. Prefer narrow inspection: call path, data shape, state transition, config, dependency version, environment, or UI event sequence. Form one hypothesis at a time and test it with the smallest useful observation.

6. Add or identify a regression check.
   For code behavior, create or identify a failing automated test that fails for the same reason as the bug. For docs, CLI setup, UI-only, or environment failures, define a deterministic smoke check or manual verification path. Confirm the check fails before changing production behavior whenever feasible.

7. Make the smallest fix.
   Change only the root-cause path. Avoid opportunistic refactors, broad rewrites, formatting churn, dependency swaps, or unrelated test edits.

8. Verify the fix.
   Rerun the regression check and the original failing command or repro path. Then run the smallest broader check that protects nearby behavior, such as the relevant test file, package test, docs checker, type check, linter, or manual UI flow.

9. Report evidence and route the next step.
   Explain root cause, fix, verification, skipped checks, and residual risk. If fixed, route to `verification-runner` for independent verification or `bug-focused-reviewer` before commit/ship. If unresolved, report the exact blocker and the next diagnostic.

## Subagent Use

Use subagents only when the user or prior plan explicitly asks for them, or when read-only parallel diagnosis is clearly separable. The main agent owns the final root-cause claim and any edits.

Good subagent splits:

- Log triage: summarize failing output and recurring error signatures.
- Test history: inspect nearby tests and identify the best regression location.
- Contract check: compare behavior against docs, interfaces, and acceptance criteria.
- Environment check: inspect dependency versions, scripts, or CI config.

Do not let multiple subagents edit the same files. Do not ask a subagent to "fix everything." Give each subagent a narrow read-only question unless the user explicitly requested delegated implementation.

## Output Format

Use this report structure:

```markdown
**Reproduction**
- Command or steps:
- Result:

**Root Cause**
- Cause:
- Evidence:

**Fix**
- Files changed:
- Why this is the smallest fix:

**Regression Check**
- Check:
- RED result:
- GREEN result:

**Verification**
- Original failure rerun:
- Broader checks:

**Skipped Checks**
- ...

**Remaining Risks**
- ...

**Recommended Next Step**
- ...
```

## Common Failure Modes

| Failure mode | Correction |
| --- | --- |
| Guessing from the error message | Reproduce and trace the failing path before editing. |
| Changing the test first | Only update a test after proving the expected behavior or contract was wrong. |
| Fixing multiple suspected bugs at once | Isolate one root cause and verify it before continuing. |
| Broad rewrite | Patch the smallest boundary that explains the evidence. |
| Claiming success from one narrow pass | Rerun the original failure plus a nearby broader check. |
| Ignoring flakiness | Classify deterministic vs flaky and collect repeated evidence. |
| Losing user changes | Check `git status --short` and avoid unrelated files. |

## Routing

- After `verification-runner` fails: use `debugging-loop`.
- After `bug-focused-reviewer` finds a reproducible bug: use `debugging-loop`.
- After `debugging-loop` fixes the issue: use `verification-runner`, then `bug-focused-reviewer`.
- If the fix needs new scope or architecture: use `vertical-slice-planner`.
- If the task is a straightforward approved implementation with no known failure: use `test-first-implementer` instead.
