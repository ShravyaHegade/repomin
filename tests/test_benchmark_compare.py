import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "compare.py"
_SPEC = importlib.util.spec_from_file_location("repomin_benchmark_compare", _PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("could not load benchmark comparison utility")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def _write(directory: Path, name: str, checks: list[dict[str, object]]) -> Path:
    counts = {
        status: sum(check["status"] == status for check in checks)
        for status in ("passed", "skipped", "failed")
    }
    path = directory / name
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "python": "3.13.0",
                "platform": "test",
                **counts,
                "checks": checks,
                "selection": {
                    "only": [],
                    "exclude": [],
                    "selected": [check["name"] for check in checks],
                },
            }
        ),
        encoding="utf-8",
    )
    return path


class BenchmarkCompareTest(unittest.TestCase):
    def test_comparison_aligns_checks_and_reports_descriptive_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _write(
                root,
                "first.json",
                [
                    {"name": "alpha", "status": "passed", "duration_seconds": 1.0},
                    {"name": "beta", "status": "skipped", "duration_seconds": 0.2},
                ],
            )
            second = _write(
                root,
                "second.json",
                [
                    {"name": "alpha", "status": "passed", "duration_seconds": 3.0},
                    {"name": "gamma", "status": "failed", "duration_seconds": 0.5},
                ],
            )
            comparison = _MODULE.compare_summaries([first, second])

        self.assertEqual(2, comparison["run_count"])
        self.assertEqual(
            ["alpha", "beta", "gamma"],
            [item["name"] for item in comparison["checks"]],
        )
        alpha = comparison["checks"][0]
        self.assertEqual(["passed", "passed"], [item["status"] for item in alpha["runs"]])
        self.assertEqual(2.0, alpha["duration_seconds"]["median"])
        beta = comparison["checks"][1]
        self.assertIsNone(beta["runs"][1])
        self.assertEqual(1.2, comparison["runs"][0]["duration_seconds"])
        self.assertEqual([], comparison["runs"][0]["selection"]["only"])

    def test_rejects_duplicate_checks_and_inconsistent_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            duplicate = root / "duplicate.json"
            duplicate.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "passed": 2,
                        "skipped": 0,
                        "failed": 0,
                        "checks": [
                            {"name": "same", "status": "passed", "duration_seconds": 0},
                            {"name": "same", "status": "passed", "duration_seconds": 0},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(_MODULE.SummaryError):
                _MODULE.compare_summaries([duplicate])

            nonfinite = root / "nonfinite.json"
            nonfinite.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "passed": 1,
                        "skipped": 0,
                        "failed": 0,
                        "checks": [
                            {
                                "name": "bad",
                                "status": "passed",
                                "duration_seconds": float("nan"),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(_MODULE.SummaryError):
                _MODULE.compare_summaries([nonfinite])

    def test_rejects_inconsistent_selection_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "passed": 1,
                        "skipped": 0,
                        "failed": 0,
                        "checks": [
                            {"name": "alpha", "status": "passed", "duration_seconds": 0}
                        ],
                        "selection": {
                            "only": [],
                            "exclude": [],
                            "selected": ["different"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(_MODULE.SummaryError):
                _MODULE.compare_summaries([path])

    def test_text_output_is_explicitly_descriptive(self) -> None:
        comparison = {
            "run_count": 1,
            "runs": [
                {
                    "path": "one.json",
                    "platform": "test",
                    "passed": 1,
                    "skipped": 0,
                    "failed": 0,
                    "duration_seconds": 0.1,
                }
            ],
            "checks": [],
        }
        output = _MODULE.render_text(comparison)
        self.assertIn("descriptive; not a performance claim", output)
        self.assertIn("one.json", output)


if __name__ == "__main__":
    unittest.main()
