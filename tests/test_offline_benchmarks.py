import json
import importlib.util
import tempfile
import unittest
from pathlib import Path


_RUNNER_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "run_offline.py"
_RUNNER_SPEC = importlib.util.spec_from_file_location("repomin_offline_runner", _RUNNER_PATH)
if _RUNNER_SPEC is None or _RUNNER_SPEC.loader is None:
    raise ImportError("could not load the offline benchmark runner")
_RUNNER = importlib.util.module_from_spec(_RUNNER_SPEC)
_RUNNER_SPEC.loader.exec_module(_RUNNER)
_write_summary = _RUNNER._write_summary
_offline_main = _RUNNER.main


class OfflineBenchmarkSummaryTest(unittest.TestCase):
    def test_list_mode_is_side_effect_free_and_includes_all_fixtures(self) -> None:
        from contextlib import redirect_stdout
        from io import StringIO

        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, _offline_main(["--list"]))

        names = output.getvalue().splitlines()
        self.assertIn("python-pyproject", names)
        self.assertEqual(len(names), len(set(names)))
        self.assertGreaterEqual(len(names), 10)

    def test_writes_versioned_counts_and_check_details(self) -> None:
        checks = [
            {
                "name": "required",
                "status": "passed",
                "duration_seconds": 0.125,
            },
            {
                "name": "optional-tool",
                "status": "skipped",
                "duration_seconds": 0.001,
                "detail": "tool is not installed",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "results.json"
            _write_summary(path, checks)
            data = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(1, data["schema_version"])
        self.assertEqual(1, data["passed"])
        self.assertEqual(1, data["skipped"])
        self.assertEqual(0, data["failed"])
        self.assertEqual(checks, data["checks"])
        self.assertIn("python", data)
        self.assertIn("platform", data)


if __name__ == "__main__":
    unittest.main()
