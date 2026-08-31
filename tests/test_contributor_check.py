import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_contribution.py"
_SPEC = importlib.util.spec_from_file_location("repomin_contributor_check", _PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("could not load contributor check utility")
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)
_TEST_ROOT = Path("repomin-test-root").resolve()


class ContributorCheckTest(unittest.TestCase):
    def test_default_checks_are_portable_and_include_scripts(self) -> None:
        with patch.object(
            _MODULE, "_ruff_command", return_value=("python", "-m", "ruff")
        ):
            checks = _MODULE.build_checks(
                root=_TEST_ROOT, python="python"
            )

        self.assertEqual(
            ["Documentation", "Ruff lint", "Byte-compile", "Unit tests"],
            [check.name for check in checks],
        )
        self.assertEqual(
            (
                "python",
                str(_TEST_ROOT / "scripts" / "check_docs.py"),
                "--root",
                str(_TEST_ROOT),
            ),
            checks[0].command,
        )
        self.assertEqual(
            ("python", "-m", "ruff", "check", "src", "tests", "scripts"),
            checks[1].command,
        )
        self.assertEqual(
            ("python", "-m", "compileall", "-q", "src", "tests", "scripts"),
            checks[2].command,
        )
        self.assertIn(str(_TEST_ROOT / "src"), checks[3].env["PYTHONPATH"])

    def test_optional_flags_change_only_requested_checks(self) -> None:
        checks = _MODULE.build_checks(
            root=_TEST_ROOT,
            python="python",
            skip_lint=True,
            skip_tests=True,
            with_benchmarks=True,
        )

        self.assertEqual(
            ["Documentation", "Byte-compile", "Offline benchmarks"],
            [c.name for c in checks],
        )
        self.assertEqual(
            (
                "python",
                str(_TEST_ROOT / "benchmarks" / "run_offline.py"),
            ),
            checks[2].command,
        )

    def test_ruff_executable_is_used_when_module_is_not_installed(self) -> None:
        with patch.object(_MODULE.importlib.util, "find_spec", return_value=None):
            with patch.object(_MODULE.shutil, "which", return_value="/opt/bin/ruff"):
                command = _MODULE._ruff_command("python")

        self.assertEqual(("/opt/bin/ruff",), command)

    def test_run_checks_continues_after_a_failure_and_reports_status(self) -> None:
        checks = (
            _MODULE.Check("first", ("first",)),
            _MODULE.Check("second", ("second",)),
        )
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=1 if len(calls) == 1 else 0)

        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = _MODULE.run_checks(
                checks,
                root=_TEST_ROOT,
                runner=runner,
            )

        self.assertEqual(1, status)
        self.assertEqual([["first"], ["second"]], [call[0] for call in calls])
        self.assertIn("FAILED: first", errors.getvalue())
        self.assertIn("PASSED: second", output.getvalue())
        self.assertIn("1 check(s) failed", errors.getvalue())

    def test_compile_check_keeps_bytecode_outside_the_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.py").write_text("value = 1\n", encoding="utf-8")
            output = io.StringIO()
            errors = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(
                errors
            ):
                status = _MODULE.run_checks(
                    (
                        _MODULE.Check(
                            "compile",
                            (
                                sys.executable,
                                "-m",
                                "compileall",
                                "-q",
                                "sample.py",
                            ),
                        ),
                    ),
                    root=root,
                )

            self.assertEqual(0, status)
            self.assertEqual([], list(root.rglob("__pycache__")))

    def test_run_checks_does_not_mutate_a_supplied_environment(self) -> None:
        base_environment = {
            "PYTHONPATH": "existing",
            "KEEP_ME": "yes",
            "PYTHONPYCACHEPREFIX": "old",
            "RUFF_CACHE_DIR": "old",
        }
        seen = []

        def runner(command, **kwargs):
            seen.append(kwargs["env"])
            Path(kwargs["env"]["PYTHONPYCACHEPREFIX"]).mkdir(parents=True)
            return SimpleNamespace(returncode=0)

        status = _MODULE.run_checks(
            (_MODULE.Check("environment", ("command",), base_environment),),
            root=_TEST_ROOT,
            runner=runner,
        )

        self.assertEqual(0, status)
        self.assertEqual(
            {
                "PYTHONPATH": "existing",
                "KEEP_ME": "yes",
                "PYTHONPYCACHEPREFIX": "old",
                "RUFF_CACHE_DIR": "old",
            },
            base_environment,
        )
        self.assertEqual("existing", seen[0]["PYTHONPATH"])
        self.assertEqual("yes", seen[0]["KEEP_ME"])
        self.assertNotEqual("old", seen[0]["PYTHONPYCACHEPREFIX"])
        self.assertNotEqual("old", seen[0]["RUFF_CACHE_DIR"])
        self.assertFalse(Path(seen[0]["PYTHONPYCACHEPREFIX"]).parent.exists())

    def test_main_explains_how_to_install_missing_ruff(self) -> None:
        output = io.StringIO()
        with patch.object(_MODULE, "_ruff_available", return_value=False):
            with contextlib.redirect_stderr(output):
                status = _MODULE.main([])

        self.assertEqual(2, status)
        self.assertIn("python3 -m pip install ruff", output.getvalue())
        self.assertIn("--skip-lint", output.getvalue())


if __name__ == "__main__":
    unittest.main()
