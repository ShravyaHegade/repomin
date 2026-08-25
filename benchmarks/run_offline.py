#!/usr/bin/env python3
"""Run every network-free benchmark and report one pass/fail summary."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]


def _environment() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["CARGO_NET_OFFLINE"] = "true"
    env["GOPROXY"] = "off"
    return env


@contextmanager
def _run_repomin(
    fixture: str,
    adapter: str,
    command: str,
    extra: list[str],
    match_pattern: str | None = "ORIGINAL_FAILURE",
) -> Iterator[Path]:
    source = (ROOT / "benchmarks" / fixture).resolve()
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "result"
        args = [
            sys.executable,
            "-m",
            "repomin",
            str(source),
            "--command",
            command,
        ]
        if match_pattern is not None:
            args.extend(["--match", match_pattern])
        args.extend(
            [
                "--adapter",
                adapter,
                "--source-reducer",
                "none",
                *extra,
                "--output",
                str(output),
            ]
        )
        result = subprocess.run(
            args,
            cwd=str(ROOT),
            env=_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        yield output


def _independent(
    command: list[str],
    output: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(output),
        env=_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _check_input_controls() -> None:
    extra = [
        "--exit-code",
        "7",
        "--gitignore-recursive",
        "--keep",
        "keep-me.txt",
        "--keep",
        "kept-dir",
    ]
    with _run_repomin(
        "input-controls",
        "none",
        "python3 reproduce.py",
        extra,
        match_pattern=None,
    ) as output:
        files = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
        _require(
            files
            == {
                "reproduce.py",
                "required.txt",
                "exit-sentinel.txt",
                "keep-me.txt",
                "kept-dir/keep-nested.txt",
            },
            "input-controls produced an unexpected file set: %s" % sorted(files),
        )
        run = _independent(["python3", "reproduce.py"], output)
        _require(run.returncode == 7, "input-controls independent exit was %s" % run.returncode)


def _check_input_controls_budget() -> None:
    extra = [
        "--exit-code",
        "7",
        "--gitignore-recursive",
        "--keep",
        "keep-me.txt",
        "--keep",
        "kept-dir",
        "--max-attempts",
        "2",
    ]
    with _run_repomin(
        "input-controls",
        "none",
        "python3 reproduce.py",
        extra,
        match_pattern=None,
    ) as output:
        metadata = output.with_name(output.name + ".repomin")
        report = json.loads((metadata / "report.json").read_text(encoding="utf-8"))
        _require(report["execution"]["budget_exhausted"] is True, "budget was not exhausted")
        for name in (
            "reproduce.py",
            "required.txt",
            "exit-sentinel.txt",
            "keep-me.txt",
            "kept-dir/keep-nested.txt",
        ):
            _require((output / name).is_file(), "missing %s" % name)
        run = _independent(["python3", "reproduce.py"], output)
        _require(run.returncode == 7, "budget independent exit was %s" % run.returncode)


def _check_semantic_stub() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "benchmarks" / "semantic-stub" / "run.py")],
        cwd=str(ROOT),
        env=_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _require(result.returncode == 0, result.stderr.strip())


def _check_manifest(
    name: str,
    adapter: str,
    command: list[str],
    manifest: str,
    required: tuple[str, ...],
    forbidden: tuple[str, ...] = (),
) -> None:
    with _run_repomin(name, adapter, " ".join(command), []) as output:
        content = (output / manifest).read_text(encoding="utf-8")
        for token in required:
            _require(token in content, "%s missing %s" % (name, token))
        for token in forbidden:
            _require(token not in content, "%s still contains %s" % (name, token))
        run = _independent(command, output)
        _require(run.returncode != 0, "%s independent exit was 0" % name)
        _require(
            "ORIGINAL_FAILURE" in run.stdout + run.stderr,
            "%s independent output lacked ORIGINAL_FAILURE" % name,
        )


def _check_text_lines() -> None:
    with _run_repomin(
        "text-lines",
        "none",
        "python3 reproduce.py",
        ["--text-file", "data.txt"],
    ) as output:
        _require(
            (output / "data.txt").read_text(encoding="utf-8") == "NEEDLE\n",
            "text-lines did not reduce data.txt to the needle",
        )
        run = _independent(["python3", "reproduce.py"], output)
        _require(run.returncode == 1, "text-lines independent exit was %s" % run.returncode)
        _require(
            "ORIGINAL_FAILURE" in run.stdout + run.stderr,
            "text-lines independent output lacked failure",
        )


def _check_cargo_workspace() -> None:
    if shutil.which("cargo") is None:
        raise _Skipped("cargo is not installed")
    with _run_repomin(
        "cargo-workspace",
        "cargo",
        "CARGO_NET_OFFLINE=true cargo run -q -p app",
        [],
    ) as output:
        root = (output / "Cargo.toml").read_text(encoding="utf-8")
        app = (output / "app" / "Cargo.toml").read_text(encoding="utf-8")
        _require("unused" not in root, "root workspace still references unused member")
        _require("required-lib" in app, "app lost its required dependency")
        _require("unused-lib" not in app, "app still references unused-lib")
        _require((output / "required-lib" / "src" / "lib.rs").is_file(), "required-lib source missing")
        _require(not (output / "unused-lib").exists(), "unused-lib still present")
        run = _independent(["cargo", "run", "-q", "-p", "app"], output)
        _require(run.returncode != 0, "cargo independent exit was 0")
        _require("ORIGINAL_FAILURE" in run.stdout + run.stderr, "cargo output lacked failure")


def _check_go_module() -> None:
    if shutil.which("go") is None:
        raise _Skipped("go is not installed")
    with _run_repomin("go-module", "go", "GOPROXY=off go run .", []) as output:
        content = (output / "go.mod").read_text(encoding="utf-8")
        _require("example.com/required" in content, "go.mod lost required module")
        _require("example.com/unused" not in content, "go.mod still references unused module")
        _require("exclude" not in content, "go.mod still has exclude")
        _require("retract" not in content, "go.mod still has retract")
        _require((output / "required").is_dir(), "required module directory missing")
        _require(not (output / "unused").exists(), "unused module directory still present")
        run = _independent(["go", "run", "."], output)
        _require(run.returncode != 0, "go independent exit was 0")
        _require("ORIGINAL_FAILURE" in run.stdout + run.stderr, "go output lacked failure")


def _check_native_process() -> None:
    if os.name == "nt":
        raise _Skipped("native-process requires POSIX")
    extra = [
        "--process-failure",
        "--baseline-runs",
        "3",
        "--candidate-runs",
        "2",
        "--jobs",
        "1",
    ]
    with _run_repomin(
        "native-process",
        "none",
        "exec python3 crash.py",
        extra,
        match_pattern=None,
    ) as output:
        metadata = output.with_name(output.name + ".repomin")
        report = json.loads((metadata / "report.json").read_text(encoding="utf-8"))
        signature = report["process_failure_signature"]
        _require(
            signature
            == {"kind": "posix_signal", "code": int(signal.SIGABRT), "name": "SIGABRT"},
            "unexpected process signature: %s" % signature,
        )
        _require((output / "crash.py").is_file(), "crash.py missing")
        _require((output / "required.txt").is_file(), "required.txt missing")
        _require(not (output / "unused.txt").exists(), "unused.txt still present")
        run = _independent(["python3", "crash.py"], output)
        _require(run.returncode == -int(signal.SIGABRT), "independent signal was %s" % run.returncode)


class _Skipped(Exception):
    pass


def _write_summary(path: Path, checks: list[dict[str, object]]) -> None:
    counts = {
        "passed": sum(item["status"] == "passed" for item in checks),
        "skipped": sum(item["status"] == "skipped" for item in checks),
        "failed": sum(item["status"] == "failed" for item in checks),
    }
    summary = {
        "schema_version": 1,
        "python": "%d.%d.%d" % sys.version_info[:3],
        "platform": sys.platform,
        **counts,
        "checks": checks,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list",
        action="store_true",
        help="list benchmark names without executing them",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        metavar="PATH",
        help="write a machine-readable result summary to PATH",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="NAME",
        help="run only the named benchmark (repeatable)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="NAME",
        help="skip the named benchmark (repeatable)",
    )
    return parser.parse_args(argv)


def _checks() -> list[tuple[str, Callable[[], None]]]:
    return [
        ("input-controls", _check_input_controls),
        ("input-controls-budget", _check_input_controls_budget),
        ("semantic-stub", _check_semantic_stub),
        ("text-lines", _check_text_lines),
        (
            "python-pyproject",
            lambda: _check_manifest(
                "python-pyproject",
                "python",
                ["python3", "reproduce.py"],
                "pyproject.toml",
                ("repomin-pyproject-fixture", "repomin-required==1.0"),
                ("repomin-unused-",),
            ),
        ),
        (
            "node-package",
            lambda: _check_manifest(
                "node-package",
                "node",
                ["python3", "reproduce.py"],
                "package.json",
                ("required-sdk", "packages/required"),
                ("unused-sdk", "unused-test-tool"),
            ),
        ),
        (
            "pipenv-package",
            lambda: _check_manifest(
                "pipenv-package",
                "pipenv",
                ["python3", "reproduce.py"],
                "Pipfile",
                ("required-package",),
                ("unused-package", "unused-test"),
            ),
        ),
        (
            "composer-package",
            lambda: _check_manifest(
                "composer-package",
                "composer",
                ["python3", "reproduce.py"],
                "composer.json",
                ("repomin/required", "autoload", "psr-4"),
            ),
        ),
        (
            "dotnet-project",
            lambda: _check_manifest(
                "dotnet-project",
                "dotnet",
                ["python3", "reproduce.py"],
                "fixture.csproj",
                ("PackageReference", "ProjectReference", "TargetFramework"),
            ),
        ),
        (
            "dotnet-directory-build-props",
            lambda: _check_manifest(
                "dotnet-directory-build-props",
                "dotnet",
                ["python3", "reproduce.py"],
                "Directory.Build.props",
                ("PackageReference", "ProjectReference", "TargetFramework"),
            ),
        ),
        (
            "ruby-gemfile",
            lambda: _check_manifest(
                "ruby-gemfile",
                "ruby",
                ["ruby", "reproduce.rb"],
                "Gemfile",
                ("repomin-required",),
            ),
        ),
        ("cargo-workspace", _check_cargo_workspace),
        ("go-module", _check_go_module),
        ("native-process", _check_native_process),
    ]


def _select_checks(
    checks: list[tuple[str, Callable[[], None]]],
    only: Sequence[str],
    exclude: Sequence[str],
) -> list[tuple[str, Callable[[], None]]]:
    available = {name for name, _ in checks}
    unknown = sorted((set(only) | set(exclude)) - available)
    if unknown:
        raise SystemExit("unknown benchmark name(s): %s" % ", ".join(unknown))
    overlap = sorted(set(only) & set(exclude))
    if overlap:
        raise SystemExit(
            "benchmark name(s) cannot be both --only and --exclude: %s"
            % ", ".join(overlap)
        )
    excluded = set(exclude)
    selected = set(only) if only else available
    filtered = [
        (name, check)
        for name, check in checks
        if name in selected and name not in excluded
    ]
    if not filtered:
        raise SystemExit("benchmark selection is empty")
    return filtered


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    checks = _checks()
    if args.list:
        if args.json_output is not None:
            raise SystemExit("--list cannot be combined with --json-output")
        for name, _ in checks:
            print(name)
        return 0
    checks = _select_checks(checks, args.only, args.exclude)
    passed = 0
    skipped = 0
    failed = 0
    results: list[dict[str, object]] = []
    for name, check in checks:
        started = time.monotonic()
        try:
            check()
            passed += 1
            results.append(
                {
                    "name": name,
                    "status": "passed",
                    "duration_seconds": round(time.monotonic() - started, 3),
                }
            )
            print("PASS %s" % name)
        except _Skipped as skip:
            skipped += 1
            results.append(
                {
                    "name": name,
                    "status": "skipped",
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "detail": str(skip),
                }
            )
            print("SKIP %s (%s)" % (name, skip))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            results.append(
                {
                    "name": name,
                    "status": "failed",
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "error": str(exc)[:2000],
                }
            )
            print("FAIL %s: %s" % (name, exc))
    if args.json_output is not None:
        try:
            _write_summary(args.json_output, results)
        except OSError as exc:
            print("could not write benchmark JSON: %s" % exc, file=sys.stderr)
            failed += 1
    print(
        "offline benchmarks: %d passed, %d skipped, %d failed"
        % (passed, skipped, failed)
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
