---
name: contain-workflow-failures
description: Use when implementing or reviewing pipelines, orchestrators, batch jobs, multi-step commands, optional effects, guard clauses, validation, retries, or error handling where one capability's failure or restriction could incorrectly abort independent work.
---

# Contain Workflow Failures

## Principle

A failure crosses only the dependency edges that require its missing output. Overall status and continued execution are separate decisions.

## Required Workflow

1. Before editing, write a Failure Boundary Map for every touched step:
   - owner and produced output;
   - prerequisites;
   - dependents;
   - failure class: task-fatal, branch-local, retryable, skipped, or cancellation;
   - diagnostic and final-status effect.
2. Confirm the map from code, tests, and product rules. Do not infer that sequential code means semantic dependency.
3. Put each guard at the narrowest owning boundary. A selector check cannot guard console persistence; an optional-provider check cannot guard the core result.
4. Convert exceptions to an explicit branch outcome at that boundary. Preserve cause, owner, stage, and useful context.
5. Run every branch whose actual prerequisites remain available. Suppress only true dependents.
6. Aggregate outcomes at the workflow boundary, then apply the existing public status contract.

## Failure Classes

Use task-fatal only when continuation is unsafe or meaningless for every branch: explicit cancellation, absent task identity, corrupted shared state, failed mandatory initialization used by all work, or a product rule that requires atomicity.

Treat provider errors, malformed branch inputs, unsupported branch modes, optional persistence failures, and individual item failures as local unless evidence shows all remaining work depends on them.

If the public API exposes only `SUCCESS`/`FAILED`, independent work still continues. Return `FAILED` when its contract means any branch failed, but retain completed outputs and diagnostics. Do not manufacture success to hide partial failure.

## Implementation Guardrails

- Never use a top-level early return for a branch-owned prerequisite or effect failure.
- Never wrap the whole workflow in one catch that discards partial results.
- Catch only errors the boundary can classify; unexpected errors keep traceback/cause and follow the repository's fatal-error policy.
- Do not express failure policy as growing boolean flags, mode switches, or nested `if` chains. Extract a named stage, policy, result type, or effect adapter when a second independent failure rule appears.
- Do not add retries, fallback, concurrency, or a new public result model unless requested.
- Keep behavior-preserving compatibility at public entry points. If correct containment requires a public-contract migration, stop and propose that migration separately.

## Required Tests

For each branch, prove: success; local failure; dependent skip; independent continuation; diagnostic preservation; and final aggregate status. Add at least one multiple-failure test to catch accidental fail-fast control flow. Verify task-fatal and cancellation paths stop all unsafe work.

## Stop Signals

Stop and remap when reasoning says “any exception means return now,” “this guard is convenient at the top,” “failed status means no other work should run,” or “the output cannot represent partial success, so discard it.”
