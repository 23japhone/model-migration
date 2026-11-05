---
name: repo-context-loader
description: Use when starting work in an existing repository, after project rules have been initialized, before planning, coding, debugging, reviewing, or handing off work.
---

# Repo Context Loader

## Overview

Use this skill to perform a read-only project onboarding pass before any development work. The result is a concise context report that tells the user what the project is, how it is organized, how to verify work, and what risks or unknowns must be resolved before editing.

## Hard Rule

Do not edit files, run formatters, install dependencies, start long-lived services, create branches, or execute commands that change project state. This skill is for reading, searching, and summarizing only.

## What To Read

Start with the highest-authority local sources:

1. `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `.github/copilot-instructions.md`, or equivalent agent rules.
2. `README*`, `docs/`, architecture notes, roadmap, testing docs, safety docs, and workflow docs.
3. Package/build manifests such as `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `Makefile`, Docker files, CI configs, and test configs.
4. Source tree entrypoints, public interfaces, app routes, CLI entrypoints, migrations, schemas, and examples.
5. Git state with `git status --short`; optionally inspect recent commits if useful.

Use `rg` and `rg --files` for discovery. Read only enough source code to explain structure and verification paths.

## Output Report

Return a compact report with these sections:

```text
Project goal:
Current state:
Key rules:
Architecture map:
Important files:
Run/test/verify commands:
Safety and privacy constraints:
Open questions:
Recommended next vertical slice:
Files read:
```

If a section is unknown, say what evidence is missing instead of guessing.

## Reading Strategy

- Prefer project docs over inference from code.
- Treat `AGENTS.md` and equivalent rules as authoritative for how to work.
- Keep product names, commands, file paths, APIs, and framework names exact.
- Distinguish facts from assumptions.
- Mention conflicts between docs, code, and tests.
- Do not browse the web unless the user explicitly asks or the task depends on current external facts.
- Do not expose secrets. If a secret-like value appears, report only that a secret risk exists and where to inspect locally.

## Hand-Off To Next Skill

End with the next best action:

- Use a planning skill when the next request is a new feature or unclear scope.
- Use an implementation skill only after the user has an approved vertical slice.
- Use a debugging skill when there is a concrete failure, repro, or log.
- Use a review skill when there is already a diff.

The recommended next vertical slice should be small enough to verify with the commands or manual checks discovered in the report.

## Common Mistakes

- Editing files while "just loading context".
- Summarizing the repository from directory names without reading rule docs.
- Omitting verification commands.
- Hiding uncertainty instead of listing open questions.
- Recommending a broad project milestone instead of the smallest useful vertical slice.
- Treating generated docs as true when code or tests contradict them.
