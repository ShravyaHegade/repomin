#!/usr/bin/env python3
"""Run the checks used by contributors before opening a pull request.

The script intentionally uses only the Python standard library.  It is safe to
run from any working directory and never changes tracked source files.  Python
and Ruff caches are redirected to a temporary directory for each invocation.
Ruff is the one optional tool: install it with ``python3 -m pip install ruff``
or use ``--skip-lint`` for a documentation-only change.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from typing import Callable, Dict, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Check:
    """One command in the contributor preflight."""

    name: str
    command: Tuple[str, ...]
    env: Optional[Dict[str, str]] = None


def _test_environment(root: Path) -> Dict[str, str]:
    """Return an environment that imports the checkout's ``src`` tree."""

    env = dict(os.environ)
    source = str(root / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        source if not existing else source + os.pathsep + existing
    )
    return env


def build_checks(
    *,
    root: Path = ROOT,
    python: str = sys.executable,
    skip_lint: bool = False,
    skip_tests: bool = False,
    with_benchmarks: bool = False,
) -> Tuple[Check, ...]:
    """Build the deterministic command list for a contributor preflight."""

    checks: List[Check] = []
    resolved_root = root.resolve()
    checks.append(
        Check(
            "Documentation",
            (
                python,
                str(resolved_root / "scripts" / "check_docs.py"),
                "--root",
                str(resolved_root),
            ),
        )
    )
    if not skip_lint:
        checks.append(
            Check(
                "Ruff lint",
                (*_ruff_command(python), "check", "src", "tests", "scripts"),
            )
        )
    checks.append(
        Check(
            "Byte-compile",
            (python, "-m", "compileall", "-q", "src", "tests", "scripts"),
        )
    )
    if not skip_tests:
        checks.append(
            Check(
                "Unit tests",
                (python, "-m", "unittest", "discover", "-s", "tests", "-v"),
                _test_environment(root),
            )
        )
    if with_benchmarks:
        checks.append(
            Check(
                "Offline benchmarks",
                (python, str(root / "benchmarks" / "run_offline.py")),
                _test_environment(root),
            )
        )
    return tuple(checks)


def _ruff_command(python: str) -> Tuple[str, ...]:
    """Choose a Ruff module or executable available to the contributor."""

    try:
        if importlib.util.find_spec("ruff") is not None:
            return (python, "-m", "ruff")
    except (ImportError, ModuleNotFoundError, ValueError):
        pass
    executable = shutil.which("ruff")
    if executable:
        return (executable,)
    return (python, "-m", "ruff")


def _ruff_available() -> bool:
    """Return whether a Ruff module or executable is available."""

    if shutil.which("ruff"):
        return True
    try:
        return importlib.util.find_spec("ruff") is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _isolated_environment(
    base: Optional[Dict[str, str]], cache_root: Path
) -> Dict[str, str]:
    """Add per-run cache locations without mutating a caller-owned mapping."""

    environment = dict(os.environ if base is None else base)
    environment["PYTHONPYCACHEPREFIX"] = str(cache_root / "pycache")
    environment["RUFF_CACHE_DIR"] = str(cache_root / "ruff")
    return environment


def run_checks(
    checks: Sequence[Check],
    *,
    root: Path = ROOT,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> int:
    """Run checks in order, streaming their output and returning a status."""

    failures = 0
    with tempfile.TemporaryDirectory(prefix="repomin-contributor-cache-") as path:
        cache_root = Path(path)
        for check in checks:
            print("\n==> " + check.name + ": " + shlex.join(check.command))
            try:
                result = runner(
                    list(check.command),
                    cwd=str(root),
                    env=_isolated_environment(check.env, cache_root),
                )
            except OSError as exc:
                print("ERROR: could not start command: " + str(exc), file=sys.stderr)
                failures += 1
                continue
            if result.returncode:
                print(
                    "FAILED: "
                    + check.name
                    + " (exit "
                    + str(result.returncode)
                    + ")",
                    file=sys.stderr,
                )
                failures += 1
            else:
                print("PASSED: " + check.name)
    if failures:
        print(
            "\nContributor preflight failed: "
            + str(failures)
            + " check(s) failed.",
            file=sys.stderr,
        )
        return 1
    print("\nContributor preflight passed.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run ReproMin's local contributor checks."
    )
    parser.add_argument(
        "--skip-lint",
        action="store_true",
        help="skip Ruff (useful for documentation-only changes)",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="skip unit tests (used by CI when coverage runs them separately)",
    )
    parser.add_argument(
        "--with-benchmarks",
        action="store_true",
        help="also run the network-free benchmark regression suite",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if not args.skip_lint and not _ruff_available():
        print(
            "Ruff is not installed. Install it with "
            "'python3 -m pip install ruff', or rerun with --skip-lint.",
            file=sys.stderr,
        )
        return 2
    checks = build_checks(
        skip_lint=args.skip_lint,
        skip_tests=args.skip_tests,
        with_benchmarks=args.with_benchmarks,
    )
    return run_checks(checks)


if __name__ == "__main__":
    raise SystemExit(main())
