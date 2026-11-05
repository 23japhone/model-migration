"""Repository and Python function metrics for source-shape audits."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from source_shape_policy import ConfigError, Policy, matches


@dataclass(frozen=True)
class MetricRule:
    name: str
    soft: int
    hard: int
    new_hard_code: str


class _BodyMetrics(ast.NodeVisitor):
    def __init__(self) -> None:
        self.complexity = 1
        self.branches = 0
        self.statements = 0

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, ast.stmt):
            self.statements += 1
        super().generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.statements += 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.statements += 1

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.statements += 1

    def visit_If(self, node: ast.If) -> None:
        self.complexity += 1
        self.branches += 1
        if node.orelse and not (len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If)):
            self.branches += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.complexity += 1
        self.branches += 1 + bool(node.orelse)
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def visit_While(self, node: ast.While) -> None:
        self.complexity += 1
        self.branches += 1 + bool(node.orelse)
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.complexity += 1
        self.branches += 2
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.complexity += max(0, len(node.values) - 1)
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        self.complexity += len(node.handlers)
        self.branches += len(node.handlers) + bool(node.orelse) + bool(node.finalbody)
        self.generic_visit(node)

    visit_TryStar = visit_Try

    def visit_Match(self, node: ast.Match) -> None:
        self.complexity += len(node.cases)
        self.branches += len(node.cases)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.complexity += 1 + len(node.ifs)
        self.generic_visit(node)


def _parse_python(path: Path, relative: str) -> ast.Module:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ConfigError(f"cannot read Python source {relative}: {error}") from error
    try:
        tree = ast.parse(source, filename=relative)
    except SyntaxError as error:
        location = f"line {error.lineno}" if error.lineno else "unknown line"
        raise ConfigError(f"cannot parse Python source {relative} at {location}") from error
    return tree


def _store_metric(
    result: dict[str, dict[str, int | str]],
    key: str,
    entry: dict[str, int | str],
) -> None:
    if key in result:
        previous = result.pop(key)
        result[f"{key}@L{previous['line']}"] = previous
    if any(existing.startswith(f"{key}@L") for existing in result):
        key = f"{key}@L{entry['line']}"
    result[key] = entry


def _function_metrics(path: Path, relative: str) -> dict[str, dict[str, int | str]]:
    tree = _parse_python(path, relative)
    result: dict[str, dict[str, int | str]] = {}

    def collect(body: Sequence[ast.stmt], parents: tuple[str, ...] = ()) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visitor = _BodyMetrics()
                for statement in node.body:
                    visitor.visit(statement)
                name = ".".join((*parents, node.name))
                _store_metric(
                    result,
                    f"{relative}::{name}",
                    {
                        "path": relative,
                        "line": node.lineno,
                        "complexity": visitor.complexity,
                        "branches": visitor.branches,
                        "statements": visitor.statements,
                    },
                )
                collect(node.body, (*parents, node.name))
            elif isinstance(node, ast.ClassDef):
                collect(node.body, (*parents, node.name))

    collect(tree.body)
    return result


def _source_files(repo: Path, root_name: str, policy: Policy) -> list[Path]:
    root = (repo / root_name).resolve()
    if not root.is_dir() or not root.is_relative_to(repo):
        raise ConfigError(f"configured root is missing or outside repository: {root_name}")
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in policy.extensions:
            continue
        relative = path.relative_to(repo).as_posix()
        if not matches(relative, policy.exclude_globs):
            files.append(path)
    return files


def measure(repo: Path, policy: Policy) -> dict[str, dict[str, dict[str, Any]]]:
    files: dict[str, dict[str, Any]] = {}
    directories: dict[str, dict[str, Any]] = {}
    functions: dict[str, dict[str, Any]] = {}
    # Explicit test roots win when a broad source root (for example ".") overlaps them.
    for kind, roots in (("test", policy.test_roots), ("source", policy.source_roots)):
        for root_name in roots:
            for path in _source_files(repo, root_name, policy):
                relative = path.relative_to(repo).as_posix()
                if relative in files:
                    continue
                try:
                    line_count = len(path.read_text(encoding="utf-8").splitlines())
                except (OSError, UnicodeError) as error:
                    raise ConfigError(f"cannot read source file {relative}: {error}") from error
                files[relative] = {"kind": kind, "lines": line_count}
                directory = path.parent.relative_to(repo).as_posix() or "."
                entry = directories.setdefault(directory, {"kind": kind, "files": 0})
                entry["files"] += 1
                if path.suffix.lower() == ".py":
                    functions.update(_function_metrics(path, relative))
    return {
        "files": dict(sorted(files.items())),
        "directories": dict(sorted(directories.items())),
        "functions": dict(sorted(functions.items())),
    }


def rules_for(category: str, item: dict[str, Any], policy: Policy) -> tuple[MetricRule, ...]:
    thresholds = policy.thresholds
    if category == "files":
        prefix = "test_file" if item["kind"] == "test" else "source_file"
        return (
            MetricRule(
                "lines",
                thresholds[f"{prefix}_soft_lines"],
                thresholds[f"{prefix}_hard_lines"],
                "new_hard_file_hotspot",
            ),
        )
    if category == "directories":
        return (
            MetricRule(
                "files",
                thresholds["directory_soft_files"],
                thresholds["directory_hard_files"],
                "new_hard_directory_hotspot",
            ),
        )
    return (
        MetricRule(
            "complexity",
            thresholds["python_complexity_soft"],
            thresholds["python_complexity_hard"],
            "new_hard_function_hotspot",
        ),
        MetricRule(
            "branches",
            thresholds["python_branches_soft"],
            thresholds["python_branches_hard"],
            "new_hard_function_hotspot",
        ),
        MetricRule(
            "statements",
            thresholds["python_statements_soft"],
            thresholds["python_statements_hard"],
            "new_hard_function_hotspot",
        ),
    )


def exception_metric(category: str, metric: str) -> str:
    return "directory_files" if category == "directories" else metric
