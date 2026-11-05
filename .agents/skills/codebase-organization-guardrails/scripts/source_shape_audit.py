#!/usr/bin/env python3
"""Audit repository growth signals without choosing architecture for the caller."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from source_shape_baseline import (
    BaselineApprovalRequired,
    compare,
    load_baseline,
    write_baseline,
)
from source_shape_metrics import measure
from source_shape_policy import ConfigError, load_policy


def _text_report(report: dict[str, Any]) -> str:
    lines = [
        f"source-shape-audit: {report['summary']['errors']} errors, "
        f"{report['summary']['warnings']} warnings"
    ]
    for finding in report["findings"]:
        lines.append(
            f"{finding['severity'].upper()} {finding['code']} "
            f"{finding['path']} {finding['message']}"
        )
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--approve-baseline-increase")
    parser.add_argument("--format", choices=("json", "text"), default="text")
    return parser.parse_args(argv)


def _run(args: argparse.Namespace) -> tuple[object, dict[str, Any], list[dict[str, Any]]]:
    repo = args.repo_root.resolve(strict=True)
    if not repo.is_dir():
        raise ConfigError("repo root must be a directory")
    policy = load_policy(args.policy.resolve(strict=True))
    baseline_path = args.baseline.resolve() if args.baseline else None
    if args.write_baseline and baseline_path is None:
        raise ConfigError("--write-baseline requires --baseline")
    baseline = load_baseline(baseline_path) if baseline_path else None
    metrics = measure(repo, policy)
    if args.write_baseline:
        write_baseline(
            baseline_path,
            metrics,
            policy,
            baseline,
            args.approve_baseline_increase,
        )
        return policy, metrics, []
    return policy, metrics, compare(metrics, baseline, policy)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        policy, metrics, findings = _run(args)
    except BaselineApprovalRequired as error:
        print(f"source-shape-audit: {error}", file=sys.stderr)
        return 1
    except (ConfigError, FileNotFoundError, OSError) as error:
        print(f"source-shape-audit: {error}", file=sys.stderr)
        return 2

    errors = sum(item["severity"] == "error" for item in findings)
    warnings = sum(item["severity"] == "warning" for item in findings)
    report = {
        "version": 1,
        "policy_sha256": policy.digest,
        "metrics": metrics,
        "findings": findings,
        "summary": {"errors": errors, "warnings": warnings},
    }
    output = (
        json.dumps(report, indent=2, sort_keys=True)
        if args.format == "json"
        else _text_report(report)
    )
    print(output)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
