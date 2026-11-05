---
name: vertical-slice-planner
description: Use when a user has repository context and a feature idea, and needs the smallest verifiable vertical slice planned before implementation.
---

# Vertical Slice Planner

## Overview

Use this skill to convert a requested feature into one small, implementation-ready slice with explicit scope, interfaces, tests, manual checks, and acceptance criteria.

## Hard Rule

Do not edit files, implement code, run formatters, install dependencies, or perform migrations. This skill produces a plan and the exact next implementation prompt only.

## Inputs To Prefer

Use the latest repository context report when available, especially output from `repo-context-loader`. If context is missing, read the repository rules and minimal project docs first:

- `AGENTS.md`, `CLAUDE.md`, or equivalent agent rules
- `README*`, `docs/`, roadmap, architecture, workflow, and testing docs
- package/build/test manifests
- relevant entrypoints or interfaces

Ask only for product or scope decisions that cannot be discovered locally. Use conservative defaults and record assumptions.

## Slice Selection Rules

A good vertical slice:

- proves one user-visible or system-visible behavior end to end
- can be completed and verified in one focused session
- includes only the minimum UI/API/data/model/test pieces needed for that behavior
- has a clear no-op or failure path when relevant
- leaves unrelated cleanup, broad refactors, nice-to-have options, and future integrations out of scope
- can be verified by concrete commands, tests, screenshots, logs, or manual steps

If the requested feature is too large, shrink it. Prefer the smallest useful slice over a broad milestone. If the first useful slice is infrastructure-only, define the observable diagnostic output that proves it works.

## Planning Workflow

1. Restate the feature in one sentence.
2. Identify the smallest behavior that would prove meaningful progress.
3. Define what is in scope and explicitly out of scope.
4. Name the touched subsystems and expected interfaces, not every internal symbol.
5. Specify automatic tests and manual checks.
6. Decide whether subagents would materially help the implementation.
7. List risks, assumptions, and open questions.
8. End with a copyable implementation prompt for the next skill or Codex turn.

Use subagents only when work can be split into independent read-heavy checks, disjoint implementation areas, separate verification passes, or parallel research that will not edit the same files. Avoid subagents for tiny slices, tightly coupled edits, or work where the main agent needs each result before it can take the next step.

## Output Format

Return this structure:

```text
Feature:
Recommended vertical slice:
Why this is the smallest useful slice:
In scope:
Out of scope:
Interfaces and data contracts:
Implementation outline:
Automatic verification:
Manual verification:
Subagent plan:
Acceptance criteria:
Risks and assumptions:
Open questions:
Next implementation prompt:
```

Keep the implementation outline decision-complete, but do not over-specify private internals that should follow the existing codebase.

## Next Implementation Prompt

The final prompt must be directly copyable and include:

- the approved slice goal
- relevant repository context
- constraints and non-goals
- test-first or verification-first requirement
- subagent instructions when useful, including each role, ownership boundaries, whether edits are allowed, what to return, and when the main agent must wait
- exact checks to run
- final response requirements: changed files, verification results, risks or skipped checks

If subagents are not useful, the prompt should explicitly say not to spawn subagents and briefly why. If subagents are useful, make the split concrete enough that the implementer can copy it without inventing roles.

## Common Mistakes

- Planning an entire MVP instead of one slice.
- Calling a backend-only setup task a vertical slice without observable output.
- Omitting verification commands or manual checks.
- Letting model or agent decide product scope without recording assumptions.
- Hiding dependencies, migrations, external services, or secrets handling.
- Ending with advice instead of a copyable implementation prompt.
