# Code Structure Policy

Read this reference when creating or updating a project's mechanical source-shape policy.

## Policy file

Store the project-specific policy at `.codex/code-structure-policy.toml`:

```toml
version = 1
source_roots = ["src"]
test_roots = ["tests"]
include_extensions = [".py"]
exclude_globs = []

[thresholds]
source_file_soft_lines = 400
source_file_hard_lines = 800
test_file_soft_lines = 600
test_file_hard_lines = 1200
directory_soft_files = 10
directory_hard_files = 16
python_complexity_soft = 10
python_complexity_hard = 15
python_branches_soft = 12
python_branches_hard = 18
python_statements_soft = 50
python_statements_hard = 80
```

Use repository-native roots and extensions. Preserve established, stricter thresholds. Adjust defaults only from repository evidence or an explicit user decision, and record the reason in the single code-quality authority.

Do not silently exclude generated, vendored, migration, schema, or embedded files. An exception must be explicit and reviewable:

```toml
[[exceptions]]
path = "src/generated/**"
metrics = ["lines", "directory_files"]
owner = "schema-generation"
reason = "Files are reproducibly generated from the checked-in schema."
verification = "Run make generate && git diff --exit-code."
```

Allowed exception metrics are `lines`, `directory_files`, `complexity`, `branches`, and `statements`.

## Baseline and commands

Store legacy metrics at `.codex/code-structure-baseline.json`. Generate it; never hand-edit it.

```bash
<project-python> <skill-dir>/scripts/source_shape_audit.py \
  --repo-root . \
  --policy .codex/code-structure-policy.toml \
  --baseline .codex/code-structure-baseline.json \
  --write-baseline
```

Replace `<project-python>` with the repository's documented interpreter command, such as `uv run python`, `.venv/bin/python`, or `python3`. It must understand the project's Python syntax; do not use an older system interpreter that cannot parse the declared `requires-python` version. This uses the project environment but does not install a new audit dependency.

Normal verification omits `--write-baseline`. Exit `0` means no blocking finding, `1` means a hard violation or stale downward ratchet, and `2` means invalid configuration or unreadable input. Warnings still require the plan or completion report to record the structure decision.

An existing hard hotspot may remain only at or below its baseline. When a metric falls, update the baseline in the same change. Refuse an increase unless the user explicitly approves the exception; only then pass `--approve-baseline-increase` with the recorded reason.

For Python, reuse configured Ruff `C901`, `PLR0912`, and `PLR0915` checks when present. Reuse Import Linter or an equivalent boundary tool when already configured. Do not install dependencies during rule bootstrap; record a missing CI-grade boundary checker as a follow-up verification slice.
