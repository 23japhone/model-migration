"""Policy loading and explicit exception matching for source-shape audits."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 and earlier
    tomllib = None  # type: ignore[assignment]

DEFAULT_EXTENSIONS = (
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".swift",
    ".ts",
    ".tsx",
)
DEFAULT_THRESHOLDS = {
    "source_file_soft_lines": 400,
    "source_file_hard_lines": 800,
    "test_file_soft_lines": 600,
    "test_file_hard_lines": 1200,
    "directory_soft_files": 10,
    "directory_hard_files": 16,
    "python_complexity_soft": 10,
    "python_complexity_hard": 15,
    "python_branches_soft": 12,
    "python_branches_hard": 18,
    "python_statements_soft": 50,
    "python_statements_hard": 80,
}
ALLOWED_EXCEPTION_METRICS = {
    "lines",
    "directory_files",
    "complexity",
    "branches",
    "statements",
}
DEFAULT_EXCLUDES = (
    ".git/**",
    ".mypy_cache/**",
    ".pytest_cache/**",
    ".ruff_cache/**",
    ".tox/**",
    ".venv/**",
    "__pycache__/**",
    "build/**",
    "dist/**",
    "node_modules/**",
    "venv/**",
)
THRESHOLD_PAIRS = (
    ("source_file_soft_lines", "source_file_hard_lines"),
    ("test_file_soft_lines", "test_file_hard_lines"),
    ("directory_soft_files", "directory_hard_files"),
    ("python_complexity_soft", "python_complexity_hard"),
    ("python_branches_soft", "python_branches_hard"),
    ("python_statements_soft", "python_statements_hard"),
)
BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


class ConfigError(ValueError):
    """The policy or baseline cannot be interpreted safely."""


@dataclass(frozen=True)
class ExceptionRule:
    path: str
    metrics: frozenset[str]
    owner: str
    reason: str
    verification: str


@dataclass(frozen=True)
class Policy:
    source_roots: tuple[str, ...]
    test_roots: tuple[str, ...]
    extensions: frozenset[str]
    exclude_globs: tuple[str, ...]
    thresholds: dict[str, int]
    exceptions: tuple[ExceptionRule, ...]
    digest: str


def _strings(value: object, field: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{field} must be a list of strings")
    result = tuple(item for item in value if item)
    if not allow_empty and not result:
        raise ConfigError(f"{field} must contain at least one path")
    return result


def _without_comment(line: str) -> str:
    quote = ""
    escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
        elif character == "\\" and quote == '"':
            escaped = True
        elif quote:
            if character == quote:
                quote = ""
        elif character in {'"', "'"}:
            quote = character
        elif character == "#":
            return line[:index]
    return line


def _fallback_value(raw: str, line_number: int) -> object:
    normalized = {"true": "True", "false": "False"}.get(raw, raw)
    try:
        value = ast.literal_eval(normalized)
    except (SyntaxError, ValueError) as error:
        raise ValueError(f"unsupported value on line {line_number}") from error
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ValueError(f"unsupported value on line {line_number}")


def _fallback_loads(text: str) -> dict[str, Any]:
    """Parse the policy's intentionally small TOML subset on Python <3.11."""
    data: dict[str, Any] = {}
    current = data
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = _without_comment(raw_line).strip()
        if not line:
            continue
        if line == "[thresholds]":
            table = data.setdefault("thresholds", {})
            if not isinstance(table, dict):
                raise ValueError("thresholds is defined more than once")
            current = table
            continue
        if line == "[[exceptions]]":
            entries = data.setdefault("exceptions", [])
            if not isinstance(entries, list):
                raise ValueError("exceptions is defined with incompatible syntax")
            entry: dict[str, Any] = {}
            entries.append(entry)
            current = entry
            continue
        if line.startswith("["):
            raise ValueError(f"unsupported table on line {line_number}")
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not BARE_KEY.fullmatch(key):
            raise ValueError(f"invalid assignment on line {line_number}")
        if key in current:
            raise ValueError(f"duplicate key {key} on line {line_number}")
        current[key] = _fallback_value(raw_value.strip(), line_number)
    return data


def _loads(text: str) -> dict[str, Any]:
    if tomllib is not None:
        return tomllib.loads(text)
    return _fallback_loads(text)


def _read_policy(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        raw_bytes = path.read_bytes()
        data = _loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ConfigError(f"cannot read policy {path}: {error}") from error
    if data.get("version") != 1:
        raise ConfigError("policy version must be 1")
    return raw_bytes, data


def _parse_extensions(data: dict[str, Any]) -> frozenset[str]:
    extensions = frozenset(
        _strings(
            data.get("include_extensions", list(DEFAULT_EXTENSIONS)),
            "include_extensions",
            allow_empty=False,
        )
    )
    if any(not item.startswith(".") for item in extensions):
        raise ConfigError("include_extensions entries must start with a dot")
    return extensions


def _parse_thresholds(data: dict[str, Any]) -> dict[str, int]:
    configured = data.get("thresholds", {})
    if not isinstance(configured, dict):
        raise ConfigError("thresholds must be a TOML table")
    unknown = set(configured) - set(DEFAULT_THRESHOLDS)
    if unknown:
        raise ConfigError(f"unknown thresholds: {', '.join(sorted(unknown))}")
    thresholds = dict(DEFAULT_THRESHOLDS)
    for name, value in configured.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ConfigError(f"thresholds.{name} must be a positive integer")
        thresholds[name] = value
    for soft_name, hard_name in THRESHOLD_PAIRS:
        if thresholds[soft_name] >= thresholds[hard_name]:
            raise ConfigError(f"{soft_name} must be lower than {hard_name}")
    return thresholds


def _parse_exceptions(data: dict[str, Any]) -> tuple[ExceptionRule, ...]:
    exception_data = data.get("exceptions", [])
    if not isinstance(exception_data, list):
        raise ConfigError("exceptions must use [[exceptions]] tables")
    exceptions: list[ExceptionRule] = []
    for index, item in enumerate(exception_data):
        if not isinstance(item, dict):
            raise ConfigError(f"exceptions[{index}] must be a table")
        accountability = {name: item.get(name) for name in ("owner", "reason", "verification")}
        if any(
            not isinstance(value, str) or not value.strip()
            for value in accountability.values()
        ):
            raise ConfigError(
                "each exception requires owner, reason, and verification as non-empty strings"
            )
        path_value = item.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            raise ConfigError(f"exceptions[{index}].path must be a non-empty string")
        metrics = frozenset(_strings(item.get("metrics", []), f"exceptions[{index}].metrics"))
        if not metrics or not metrics <= ALLOWED_EXCEPTION_METRICS:
            allowed = ", ".join(sorted(ALLOWED_EXCEPTION_METRICS))
            raise ConfigError(f"exceptions[{index}].metrics must use: {allowed}")
        exceptions.append(
            ExceptionRule(
                path=path_value,
                metrics=metrics,
                owner=accountability["owner"],
                reason=accountability["reason"],
                verification=accountability["verification"],
            )
        )
    return tuple(exceptions)


def load_policy(path: Path) -> Policy:
    raw_bytes, data = _read_policy(path)
    return Policy(
        source_roots=_strings(data.get("source_roots"), "source_roots", allow_empty=False),
        test_roots=_strings(data.get("test_roots", []), "test_roots"),
        extensions=_parse_extensions(data),
        exclude_globs=DEFAULT_EXCLUDES
        + _strings(data.get("exclude_globs", []), "exclude_globs"),
        thresholds=_parse_thresholds(data),
        exceptions=_parse_exceptions(data),
        digest=hashlib.sha256(raw_bytes).hexdigest(),
    )


def matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) or Path(path).match(pattern) for pattern in patterns)


def exception_for(policy: Policy, path: str, metric: str) -> ExceptionRule | None:
    for rule in policy.exceptions:
        if metric in rule.metrics and matches(path, (rule.path,)):
            return rule
    return None
