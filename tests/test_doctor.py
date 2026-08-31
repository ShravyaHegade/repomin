"""Tests for the read-only ``repomin doctor`` preflight."""

from __future__ import annotations

import contextlib
import io
import json
import math
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from repomin.cli import main
from repomin.doctor import run_doctor


_REPRODUCER = """\
from pathlib import Path
import sys

if not Path("required.txt").exists():
    print("DIFFERENT_FAILURE", file=sys.stderr)
    raise SystemExit(2)
print("ORIGINAL_FAILURE", file=sys.stderr)
raise SystemExit(7)
"""


def _python_command(script: str) -> str:
    """Build a shell command for the platform-specific command runner."""
    argv = [sys.executable, script]
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


class DoctorTest(unittest.TestCase):
    def _source(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        source = directory / "project"
        source.mkdir()
        (source / "reproduce.py").write_text(_REPRODUCER, encoding="utf-8")
        (source / "required.txt").write_text("required\n", encoding="utf-8")
        (source / "pyproject.toml").write_text(
            "[project]\nname = 'doctor-fixture'\n", encoding="utf-8"
        )
        return source

    def test_static_doctor_detects_python_project_without_writing(self) -> None:
        source = self._source()
        before = sorted(path.relative_to(source).as_posix() for path in source.rglob("*"))
        ok, result = run_doctor(source)
        self.assertTrue(ok)
        self.assertTrue(result["adapters"]["python"]["detected"])
        self.assertEqual("not_run", result["baseline"]["status"])
        after = sorted(path.relative_to(source).as_posix() for path in source.rglob("*"))
        self.assertEqual(before, after)

    def test_doctor_runs_baseline_in_fresh_copies(self) -> None:
        source = self._source()
        command = _python_command("reproduce.py")
        ok, result = run_doctor(
            source,
            command=command,
            match="ORIGINAL_FAILURE",
            exit_code=7,
            adapter="python",
            source_reducer="python",
            baseline_runs=2,
        )
        self.assertTrue(ok)
        self.assertEqual("pass", result["baseline"]["status"])
        self.assertEqual(2, result["baseline"]["runs"])
        self.assertEqual(2, result["baseline"]["passes"])
        self.assertFalse((source / "doctor").exists())

    def test_doctor_uses_effective_ignores_for_size_and_detection(self) -> None:
        source = self._source()
        ignored = source / "generated"
        ignored.mkdir()
        (ignored / "Cargo.toml").write_text(
            "[package]\nname = 'ignored'\n", encoding="utf-8"
        )
        ok, result = run_doctor(
            source,
            adapter="cargo",
            ignore_names=("generated",),
        )
        self.assertFalse(ok)
        self.assertFalse(result["adapters"]["cargo"]["detected"])
        self.assertEqual(3, result["source_files"])
        self.assertIn("generated", result["ignored_names"])

    def test_doctor_applies_root_gitignore_before_detection(self) -> None:
        source = self._source()
        generated = source / "generated"
        generated.mkdir()
        (generated / "package.json").write_text(
            '{"name": "ignored"}\n', encoding="utf-8"
        )
        (source / ".gitignore").write_text("/generated/\n", encoding="utf-8")

        ok, result = run_doctor(source, gitignore=True)

        self.assertTrue(ok)
        self.assertFalse(result["adapters"]["node"]["detected"])
        self.assertEqual([".gitignore"], result["gitignore_files"])
        self.assertRegex(result["gitignore_sha256"], r"^[0-9a-f]{64}$")
        self.assertFalse(result["gitignore_recursive"])
        self.assertEqual(4, result["source_files"])

    def test_doctor_keeps_same_named_file_for_directory_only_rule(self) -> None:
        source = self._source()
        (source / "generated").write_text("ordinary file\n", encoding="utf-8")
        (source / ".gitignore").write_text("generated/\n", encoding="utf-8")

        ok, result = run_doctor(source, gitignore=True)

        self.assertTrue(ok)
        self.assertEqual(5, result["source_files"])

    def test_doctor_applies_nested_gitignore_to_baseline_and_reports_rules(self) -> None:
        source = self._source()
        services = source / "services"
        private = services / "private"
        private.mkdir(parents=True)
        (source / ".gitignore").write_text("\n", encoding="utf-8")
        (services / ".gitignore").write_text("/private/\n", encoding="utf-8")
        (private / "package.json").write_text(
            '{"name": "ignored"}\n', encoding="utf-8"
        )
        command = _python_command("reproduce.py")

        ok, result = run_doctor(
            source,
            command=command,
            match="ORIGINAL_FAILURE",
            exit_code=7,
            gitignore=True,
            gitignore_recursive=True,
        )

        self.assertTrue(ok)
        self.assertEqual("pass", result["baseline"]["status"])
        self.assertEqual([".gitignore", "services/.gitignore"], result["gitignore_files"])
        self.assertTrue(result["gitignore_recursive"])
        self.assertRegex(result["gitignore_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("services/private/package.json", result["adapters"]["node"]["files"])

    def test_cli_accepts_doctor_gitignore_options(self) -> None:
        source = self._source()
        custom = source / "custom.ignore"
        custom.write_text("generated/\n", encoding="utf-8")
        generated = source / "generated"
        generated.mkdir()
        (generated / "package.json").write_text("{}\n", encoding="utf-8")
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            exit_code = main(
                [
                    "doctor",
                    str(source),
                    "--gitignore-file",
                    "custom.ignore",
                    "--json",
                ]
            )

        self.assertEqual(0, exit_code)
        result = json.loads(output.getvalue())
        self.assertEqual(["custom.ignore"], result["gitignore_files"])
        self.assertFalse(result["adapters"]["node"]["detected"])

    def test_doctor_detection_matches_adapter_and_source_patterns(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        source = Path(temporary.name) / "project"
        source.mkdir()
        (source / "custom.gradle").write_text("dependencies {}\n", encoding="utf-8")
        requirements = source / "requirements"
        requirements.mkdir()
        (requirements / "base.txt").write_text("required==1\n", encoding="utf-8")
        (source / "requirements.in").write_text("ignored==1\n", encoding="utf-8")
        (source / "Gemfile.lock").write_text("GEM\n", encoding="utf-8")
        (source / "UPPER.PY").write_text("pass\n", encoding="utf-8")

        ok, result = run_doctor(source)

        self.assertTrue(ok)
        self.assertTrue(result["adapters"]["gradle"]["detected"])
        self.assertTrue(result["adapters"]["python"]["detected"])
        self.assertFalse(result["adapters"]["ruby"]["detected"])
        self.assertFalse(result["source_reducers"]["python"]["detected"])

    def test_doctor_baseline_preserves_the_output_basename(self) -> None:
        source = self._source()
        (source / "reproduce.py").write_text(
            """\
from pathlib import Path
import sys

if Path.cwd().name != "stable-output-name":
    raise SystemExit(2)
print("ORIGINAL_FAILURE", file=sys.stderr)
raise SystemExit(7)
""",
            encoding="utf-8",
        )
        command = _python_command("reproduce.py")
        ok, result = run_doctor(
            source,
            command=command,
            match="ORIGINAL_FAILURE",
            exit_code=7,
            output=str(source.parent / "stable-output-name"),
        )
        self.assertTrue(ok)
        self.assertEqual("pass", result["baseline"]["status"])

    def test_doctor_rejects_existing_output_and_sidecar(self) -> None:
        source = self._source()
        output = source.parent / "result"
        output.mkdir()
        ok, result = run_doctor(source, output=str(output))
        self.assertFalse(ok)
        self.assertTrue(
            any(
                check["name"] == "output"
                and check["status"] == "fail"
                and "already exists" in check["message"]
                for check in result["checks"]
            )
        )

        output.rmdir()
        metadata = output.with_name(output.name + ".repomin")
        metadata.mkdir()
        ok, result = run_doctor(source, output=str(output))
        self.assertFalse(ok)
        self.assertTrue(
            any(
                check["name"] == "output"
                and "metadata output already exists" in check["message"]
                for check in result["checks"]
            )
        )

    @unittest.skipIf(os.name == "nt", "symlink creation is not generally available")
    def test_doctor_rejects_a_dangling_output_symlink(self) -> None:
        source = self._source()
        output = source.parent / "result"
        output.symlink_to(source.parent / "missing-target", target_is_directory=True)

        ok, result = run_doctor(source, output=str(output))

        self.assertFalse(ok)
        self.assertTrue(
            any(
                check["name"] == "output"
                and check["status"] == "fail"
                and "symbolic link" in check["message"]
                for check in result["checks"]
            )
        )

    def test_doctor_rejects_invalid_baseline_and_host_docker_options(self) -> None:
        source = self._source()
        ok, result = run_doctor(
            source,
            baseline_runs=0,
            timeout=math.inf,
            docker_image="example:local",
            docker_network="bridge",
        )
        self.assertFalse(ok)
        failures = [
            check["message"]
            for check in result["checks"]
            if check["status"] == "fail"
        ]
        self.assertTrue(any("baseline runs" in message for message in failures))
        self.assertTrue(any("finite number" in message for message in failures))
        self.assertTrue(any("requires --backend docker" in message for message in failures))

    def test_doctor_reports_bad_oracle_and_output_without_running(self) -> None:
        source = self._source()
        ok, result = run_doctor(
            source,
            command="false",
            output=str(source / "inside"),
            adapter="cargo",
        )
        self.assertFalse(ok)
        failures = {
            check["name"]: check["message"]
            for check in result["checks"]
            if check["status"] == "fail"
        }
        self.assertIn("output", failures)
        self.assertIn("adapter", failures)
        self.assertIn("oracle", failures)
        self.assertEqual("not_run", result["baseline"]["status"])

    def test_cli_emits_json_doctor_result(self) -> None:
        source = self._source()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["doctor", str(source), "--json"])
        self.assertEqual(0, exit_code)
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["ok"])
        self.assertEqual(str(source.resolve()), result["source"])

    def test_cli_returns_one_when_baseline_does_not_reproduce(self) -> None:
        source = self._source()
        stdout = io.StringIO()
        command = _python_command("reproduce.py")
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "doctor",
                    str(source),
                    "--command",
                    command,
                    "--match",
                    "NO_SUCH_FAILURE",
                    "--json",
                ]
            )
        self.assertEqual(1, exit_code)
        result = json.loads(stdout.getvalue())
        self.assertFalse(result["ok"])
        self.assertEqual("fail", result["baseline"]["status"])


if __name__ == "__main__":
    unittest.main()
