---
name: maintainable-code-guardrails
description: Use when adding or modifying code, reviewing changed code for maintainability, changing a hotspot, or drafting project rules for structure, complexity, boundaries, testing, and code quality.
---

# Maintainable Code Guardrails

## Principle

Make the smallest verifiable change that leaves touched code cohesive. This does not authorize unrelated cleanup.

## Required Workflow

1. Read task, rules, nearby code, tests, and verification commands.
2. State behavior, touched units, boundaries, and non-goals.
3. **REQUIRED SUB-SKILL:** Use `codebase-organization-guardrails` when adding a responsibility, module, feature, entry point, sharing, dependency, public contract, or layout change. It owns placement and the Source Tree Contract.
4. Record pre-change file lines and, for touched Python functions, complexity, branches, statements, and parameter count. Prefer the project's tools; otherwise use `source-shape-audit` from that sub-skill.
5. Use `test-first-implementer` for behavior changes when its prerequisites are satisfied.
6. Implement the simplest project-idiomatic design.
7. Re-measure, self-review, then use `verification-runner` before completion.

## Complexity Ratchet

- Soft thresholds force a documented structural review; numbers do not force a split.
- Hard thresholds block a new file or function from becoming a hotspot.
- Register an existing hard violation as a legacy hotspot. Do not increase its lines, complexity, branches, statements, parameters, or responsibilities.
- When a hotspot improves, update its baseline in the same change; never loosen a baseline without explicit user approval.
- Adding `if` branches, early returns, mode switches, or boolean parameters to a complex workflow signals a new responsibility. First extract a policy, workflow stage, or effect adapter behind the existing public boundary.
- If a safe extraction changes public behavior or requires broad migration, stop the feature change and propose a separate migration plan.
- A split must create a meaningful owner or test/effect boundary and retain behavior tests. Moving text or making one-line forwarding modules is not decoupling.

Default thresholds are defined by `codebase-organization-guardrails`: source files 400/800 lines, tests 600/1200, peer source files 10/16, and Python complexity 10/15, branches 12/18, statements 50/80. Repository evidence may override them in the machine-readable policy. Generated, vendor, migration, schema, and embedded content need explicit owner, reason, and verification; labels alone do not exempt them.

## Other Guardrails

- One explainable purpose per touched unit; no god objects, catch-all managers, hidden coupling, or mixed abstraction levels.
- Use domain names and direct control flow. Abstractions need current evidence; reject speculative interfaces and catch-all `utils`, `helpers`, `common`, or type buckets.
- Remove duplicated knowledge, not every similar line.
- Keep I/O, mutation, dependencies, and errors visible. Never swallow errors, report false success, or discard diagnostic context.
- Changed behavior needs an automated test or exact repeatable check with risk and recovery documented.

## Exceptions and Stop Conditions

User instructions and valid repository rules take precedence. Record every exception's owner, reason, impact, and verification. Ask before changing architecture, public contracts, task scope, or a hard baseline.

Stop and revisit the design if reasoning sounds like “just two more branches,” “the file is already large,” “a wrapper counts as a split,” or “raise the baseline so CI passes.”
