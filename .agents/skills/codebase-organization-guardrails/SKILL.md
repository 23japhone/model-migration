---
name: codebase-organization-guardrails
description: Use when adding a module, feature, entry point, file, or responsibility; growing a flat package or large hotspot; moving or splitting files; changing imports, dependency direction, sharing, or public contracts; reviewing source layout; or drafting source-tree rules.
---

# Codebase Organization Guardrails

Prevent touched code from becoming a flat package, god module, or dependency tangle while preserving coherent repository-native boundaries. This skill solely owns source-organization decisions and the Source Tree Contract.

## Required Workflow

1. Inspect rules, manifests, source/test roots, entry points, representative imports, nearby modules/tests, and boundary tools. Classify the actual organization and surface conflicting evidence.
2. Run native structure checks. When `.codex/code-structure-policy.toml` exists, run this skill's `scripts/source_shape_audit.py`. Read [code-structure-policy.md](references/code-structure-policy.md) only when creating or changing that policy.
3. Before editing, produce a **Change Shape Map** with one row per created, moved, or responsibility-changing file:

| Path | Semantic owner | One responsibility/change reason | Current consumer | Public/private | Allowed dependencies | Expected growth | Placement reason |
| --- | --- | --- | --- | --- | --- | --- | --- |

4. Preserve an established coherent layout. For new or undecided code, start capability-first and add internal roles only when current behavior proves them.
5. Verify the final tree, imports, public contracts, structure baseline, boundary checks, and behavior tests. Report every warning, exception, and deferred migration.

## Invariants

- Keep one coherent feature in one file. Promote it to a package when a second independent change reason, effect boundary, or test boundary appears.
- Keep one semantic owner per directory. Multiple independent capability owners require grouping before another peer is added; deadlines do not waive this rule.
- Soft thresholds require a recorded design decision. Block new hard hotspots. A legacy hard hotspot may not gain lines, branches, statements, responsibilities, or peer-file pressure beyond its baseline.
- Add behavior to a legacy hotspot through a focused owner module behind the narrowest existing contract. If that needs a public or broad source migration, stop for a separately approved migration plan.
- Keep cross-capability dependencies one-way and acyclic. Consume another owner only through its narrow public entry; forbid deep internal imports.
- Keep composition roots to parsing, mapping, assembly, and delegation. Put effects at identifiable repository-native boundaries.
- Introduce interfaces only at real variable seams. Reject empty layers, speculative facades, shallow wrappers, and one-interface-per-class designs.
- Share code only with stable semantics, an accountable owner, current cross-owner reuse, and one change reason. Reject `utils`, `helpers`, `common`, `shared`, and type buckets.
- Protect splits with behavior tests. Do not move unrelated code while touching a hotspot.

## Local Instructions

Do not create `AGENTS.md` in every directory. Add one only at a semantic module root with an owner and a command, dependency rule, contract, or safety constraint that differs from its parent. Store only the delta. Root `AGENTS.md` indexes local files; cross-module tasks explicitly read every affected file before planning.

## Source Tree Contract

Record source/test roots; organization mode; owners and internal roles; composition/effect boundaries; dependency direction; public and deep-import policy; tests and sharing; policy/baseline paths; thresholds, hotspots, ratchets, and exceptions; package-promotion rules; migration approval; and local-instruction routing in the single code-quality authority.

## Red Flags

| Rationalization | Response |
| --- | --- |
| “Adding branches is the smallest diff.” | A hard or legacy hotspot cannot grow; extract the responsibility or stop. |
| “Keep the MVP flat and clean it later.” | Group multiple current owners now. |
| “Split everything at the threshold.” | Numbers trigger judgment; split only at a real seam. |
| “Add wrappers so it looks layered.” | Reject shallow abstractions. |
