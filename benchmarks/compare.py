#!/usr/bin/env python3
"""Compare network-free benchmark summaries without making speed claims."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
_STATUSES = ("passed", "skipped", "failed")


class SummaryError(ValueError):
    """Raised when a benchmark summary is not compatible with this tool."""


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SummaryError("%s must be a number" % label)
    if not math.isfinite(value) or value < 0:
        raise SummaryError("%s must be a finite non-negative number" % label)
    return float(value)


def load_summary(path: Path) -> Mapping[str, Any]:
    """Load and validate one ``run_offline.py`` JSON summary."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SummaryError("could not read %s: %s" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise SummaryError("%s must contain a JSON object" % path)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise SummaryError(
            "%s has unsupported schema_version %r"
            % (path, data.get("schema_version"))
        )
    checks = data.get("checks")
    if not isinstance(checks, list):
        raise SummaryError("%s checks must be a JSON array" % path)
    names: set[str] = set()
    counts = {status: 0 for status in _STATUSES}
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise SummaryError("%s checks[%d] must be an object" % (path, index))
        name = check.get("name")
        status = check.get("status")
        if not isinstance(name, str) or not name:
            raise SummaryError("%s checks[%d] has no name" % (path, index))
        if name in names:
            raise SummaryError("%s contains duplicate check %r" % (path, name))
        names.add(name)
        if status not in _STATUSES:
            raise SummaryError("%s checks[%d] has invalid status %r" % (path, index, status))
        counts[status] += 1
        _number(check.get("duration_seconds"), "%s checks[%d] duration_seconds" % (path, index))
    for status in _STATUSES:
        if data.get(status) != counts[status]:
            raise SummaryError(
                "%s %s count does not match checks" % (path, status)
            )
    return data


def _check_by_name(summary: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {check["name"]: check for check in summary["checks"]}


def compare_summaries(paths: Iterable[Path]) -> dict[str, Any]:
    """Build a deterministic descriptive comparison for summary files."""
    inputs = list(paths)
    if not inputs:
        raise SummaryError("at least one summary path is required")
    summaries = [load_summary(path) for path in inputs]
    by_name = [_check_by_name(summary) for summary in summaries]
    names = sorted({name for checks in by_name for name in checks})

    runs = []
    for path, summary in zip(inputs, summaries):
        checks = summary["checks"]
        duration = sum(float(check["duration_seconds"]) for check in checks)
        runs.append(
            {
                "path": str(path),
                "python": summary.get("python"),
                "platform": summary.get("platform"),
                "passed": summary["passed"],
                "skipped": summary["skipped"],
                "failed": summary["failed"],
                "duration_seconds": round(duration, 3),
            }
        )

    checks_output = []
    for name in names:
        observations = []
        durations = []
        status_counts = {status: 0 for status in _STATUSES}
        for checks in by_name:
            check = checks.get(name)
            if check is None:
                observations.append(None)
                continue
            duration = round(float(check["duration_seconds"]), 3)
            observations.append(
                {
                    "status": check["status"],
                    "duration_seconds": duration,
                }
            )
            durations.append(duration)
            status_counts[check["status"]] += 1
        checks_output.append(
            {
                "name": name,
                "runs": observations,
                "status_counts": status_counts,
                "duration_seconds": {
                    "min": min(durations),
                    "median": round(statistics.median(durations), 3),
                    "max": max(durations),
                }
                if durations
                else None,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "run_count": len(runs),
        "runs": runs,
        "checks": checks_output,
    }


def _format_status(observation: Mapping[str, Any] | None) -> str:
    if observation is None:
        return "missing"
    return "%s (%.3fs)" % (observation["status"], observation["duration_seconds"])


def render_text(comparison: Mapping[str, Any]) -> str:
    lines = [
        "benchmark comparison (descriptive; not a performance claim)",
        "runs: %d" % comparison["run_count"],
    ]
    for index, run in enumerate(comparison["runs"], start=1):
        lines.append(
            "run %d: %s | %s | passed=%d skipped=%d failed=%d total=%.3fs"
            % (
                index,
                run["path"],
                run["platform"],
                run["passed"],
                run["skipped"],
                run["failed"],
                run["duration_seconds"],
            )
        )
    if comparison["checks"]:
        lines.append("")
        lines.append(
            "check | "
            + " | ".join(
                "run %d" % i
                for i in range(1, comparison["run_count"] + 1)
            )
        )
        lines.append("--- | " + " | ".join("---" for _ in range(comparison["run_count"])))
        for check in comparison["checks"]:
            lines.append(
                "%s | %s"
                % (
                    check["name"],
                    " | ".join(_format_status(item) for item in check["runs"]),
                )
            )
    return "\n".join(lines) + "\n"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summaries", nargs="+", type=Path, metavar="SUMMARY.json")
    parser.add_argument(
        "--json-output",
        type=Path,
        metavar="PATH",
        help="also write the normalized comparison as JSON",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        comparison = compare_summaries(args.summaries)
        if args.json_output is not None:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(
                json.dumps(comparison, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    except (OSError, SummaryError) as exc:
        print("could not compare benchmark summaries: %s" % exc, file=sys.stderr)
        return 2
    print(render_text(comparison), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
