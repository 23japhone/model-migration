---
name: preserve-task-continuity
description: Use when executing a multi-step plan, verification suite, release checklist, migration, research run, batch of tool calls, or other task where one failed step could cause Codex to abandon independent remaining work or lose partial results.
---

# Preserve Task Continuity

## Principle

Continue every authorized step whose real prerequisites remain satisfied. A command's short-circuit behavior is not the task dependency graph.

## Required Workflow

1. Before execution, create a Task Ledger with each step's:
   - concrete outcome;
   - actual prerequisites and dependents;
   - status: pending, running, succeeded, failed, blocked, skipped, or cancelled;
   - evidence location or command;
   - retry or recovery rule, if any.
2. Mark only genuine data, state, safety, authorization, or user-decision dependencies. Sequence, shared tooling, or `&&` placement alone does not create a dependency.
3. Execute ready steps. Parallelize independent checks when safe; otherwise run them separately so one exit code cannot suppress the rest.
4. After a failure:
   - capture exact error and partial outputs;
   - classify it as task-fatal, local, retryable, or cancellation;
   - mark true dependents blocked with the missing prerequisite;
   - immediately continue other ready steps.
5. Recompute readiness after every material result. Retry only when authorized and when evidence suggests the retry can differ; never loop on the same failure.
6. Finish with an aggregate report of succeeded work, failures, blocked/skipped steps, retained outputs, uncovered verification, and the next action needed.

## Stop Conditions

Stop all remaining work only for explicit cancellation, lost authorization, unsafe or destructive continuation, corrupted shared state, exhausted global resource required by every step, or a missing user choice that changes the task materially.

Do not stop globally because one test failed, one provider is unavailable, one artifact could not be generated, or a shell chain returned nonzero. Run independent checks to learn the full state.

## Execution Guardrails

- Do not report “not run because the command stopped” until checking whether the suppressed steps can run independently.
- Do not rerun successful work merely because another step failed.
- Do not erase or overwrite successful partial outputs during cleanup or retry.
- Do not bypass approvals, expand scope, publish, commit, or mutate external state merely to keep moving.
- Do not claim overall success when required steps failed. Continuity improves evidence; it does not weaken acceptance criteria.
- If implementing these semantics inside project code, also use `contain-workflow-failures` to place failure boundaries correctly.

## Recovery Examples

- `test && lint && docs` stops at tests: record the test failure, then run lint and docs separately if independent.
- One platform build fails: continue other platform builds unless they consume its artifact.
- Data extraction fails: analyze already-produced datasets and run unrelated source checks; block only consumers of the missing data.

## Completion Check

Every ledger row has a terminal status and evidence. Every blocked or skipped row names its prerequisite or decision. The final result distinguishes task failure from useful completed work and preserves all actionable diagnostics.
