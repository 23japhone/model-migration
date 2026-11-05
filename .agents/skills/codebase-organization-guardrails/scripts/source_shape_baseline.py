"""Baseline comparison and ratcheting for source-shape audits."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from source_shape_metrics import MetricRule, exception_metric, rules_for
from source_shape_policy import ConfigError, Policy, exception_for

BASELINE_FIELDS: dict[str, dict[str, type]] = {
    "files": {"kind": str, "lines": int},
    "directories": {"kind": str, "files": int},
    "functions": {
        "path": str,
        "line": int,
        "complexity": int,
        "branches": int,
        "statements": int,
    },
}


class BaselineApprovalRequired(RuntimeError):
    """A baseline write would legitimize a hard-metric increase."""


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    path: str
    metric: str
    current: int | None
    rule: MetricRule
    baseline: int | None = None

    def as_dict(self) -> dict[str, Any]:
        message = (
            f"{self.metric}={self.current}; soft={self.rule.soft}; hard={self.rule.hard}"
        )
        if self.baseline is not None:
            message += f"; baseline={self.baseline}"
        return {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "metric": self.metric,
            "current": self.current,
            "baseline": self.baseline,
            "soft": self.rule.soft,
            "hard": self.rule.hard,
            "message": message,
        }


def _select_finding(
    path: str, current: int, baseline: int | None, rule: MetricRule
) -> Finding | None:
    if baseline is not None and current < baseline:
        return Finding(
            "error", "baseline_tighten_required", path, rule.name, current, rule, baseline
        )
    if baseline is not None and baseline > rule.hard and current > baseline:
        return Finding(
            "error", "legacy_hotspot_worsened", path, rule.name, current, rule, baseline
        )
    if current > rule.hard and (baseline is None or baseline <= rule.hard):
        return Finding("error", rule.new_hard_code, path, rule.name, current, rule, baseline)
    if current > rule.soft:
        return Finding(
            "warning",
            "soft_structure_review_required",
            path,
            rule.name,
            current,
            rule,
            baseline,
        )
    return None


def _compare_item(
    category: str,
    path: str,
    item: dict[str, Any],
    previous: dict[str, Any] | None,
    policy: Policy,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for rule in rules_for(category, item, policy):
        metric = exception_metric(category, rule.name)
        if exception_for(policy, item.get("path", path), metric):
            continue
        baseline = int(previous[rule.name]) if previous and rule.name in previous else None
        finding = _select_finding(path, int(item[rule.name]), baseline, rule)
        if finding:
            findings.append(finding.as_dict())
    return findings


def _removed_findings(
    category: str,
    current_items: dict[str, dict[str, Any]],
    previous_items: dict[str, dict[str, Any]],
    policy: Policy,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path, previous in previous_items.items():
        if path in current_items:
            continue
        for rule in rules_for(category, previous, policy):
            old = int(previous[rule.name])
            findings.append(
                Finding(
                    "error",
                    "baseline_tighten_required",
                    path,
                    rule.name,
                    None,
                    rule,
                    old,
                ).as_dict()
            )
    return findings


def compare(
    metrics: dict[str, dict[str, dict[str, Any]]],
    baseline: dict[str, Any] | None,
    policy: Policy,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    baseline_metrics = baseline.get("metrics", {}) if baseline else {}
    for category, items in metrics.items():
        previous_items = baseline_metrics.get(category, {})
        for path, item in items.items():
            findings.extend(_compare_item(category, path, item, previous_items.get(path), policy))
        findings.extend(_removed_findings(category, items, previous_items, policy))
    return sorted(findings, key=lambda item: (item["severity"], item["path"], item["metric"]))


def _valid_baseline_field(value: object, expected_type: type) -> bool:
    if expected_type is int:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return isinstance(value, str) and bool(value)


def _validate_baseline_metrics(metrics: object) -> None:
    if not isinstance(metrics, dict) or set(metrics) != set(BASELINE_FIELDS):
        raise ConfigError("baseline metrics must contain files, directories, and functions")
    for category, required_fields in BASELINE_FIELDS.items():
        items = metrics[category]
        if not isinstance(items, dict):
            raise ConfigError(f"baseline metrics.{category} must be an object")
        for path, item in items.items():
            if not isinstance(path, str) or not path or not isinstance(item, dict):
                raise ConfigError(f"baseline metrics.{category} entries must be named objects")
            for field, expected_type in required_fields.items():
                value = item.get(field)
                if not _valid_baseline_field(value, expected_type):
                    raise ConfigError(
                        f"baseline metrics.{category}.{path}.{field} has invalid type or value"
                    )
            if category in {"files", "directories"} and item["kind"] not in {"source", "test"}:
                raise ConfigError(f"baseline metrics.{category}.{path}.kind is invalid")


def load_baseline(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigError(f"cannot read baseline {path}: {error}") from error
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
    ):
        raise ConfigError("baseline must be a version 1 source-shape document")
    _validate_baseline_metrics(data.get("metrics"))
    return data


def _baseline_increases(
    current: dict[str, dict[str, dict[str, Any]]],
    baseline: dict[str, Any],
    policy: Policy,
) -> bool:
    previous_metrics = baseline["metrics"]
    for category, items in current.items():
        previous_items = previous_metrics.get(category, {})
        for path, item in items.items():
            previous = previous_items.get(path)
            if not previous:
                if any(
                    int(item[rule.name]) > rule.hard
                    for rule in rules_for(category, item, policy)
                ):
                    return True
                continue
            for rule in rules_for(category, item, policy):
                old = int(previous[rule.name])
                now = int(item[rule.name])
                if now > old and (old > rule.hard or now > rule.hard):
                    return True
    return False


def write_baseline(
    path: Path,
    metrics: dict[str, dict[str, dict[str, Any]]],
    policy: Policy,
    previous: dict[str, Any] | None,
    approval: str | None,
) -> None:
    if previous and _baseline_increases(metrics, previous, policy) and not approval:
        raise BaselineApprovalRequired(
            "baseline would increase a hard hotspot; rerun only after user approval with "
            "--approve-baseline-increase REASON"
        )
    payload: dict[str, Any] = {
        "version": 1,
        "policy_sha256": policy.digest,
        "metrics": metrics,
    }
    if approval:
        payload["approval"] = {"reason": approval}
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as temporary:
            json.dump(payload, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
