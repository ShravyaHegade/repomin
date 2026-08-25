import json
import tempfile
import unittest
from pathlib import Path

from repomin.cli import main
from repomin.report import (
    ReportValidationError,
    validate_report_document,
    validate_report_file,
)
from repomin.session import _tree_digest


def _report() -> dict:
    return {
        "schema_version": 1,
        "command": "python3 reproduce.py",
        "failure_match": "FAIL",
        "baseline_exit_code": 1,
        "final_exit_code": 1,
        "source": {"files": 2, "bytes": 10},
        "output": {"files": 1, "bytes": 5},
        "attempts": 1,
        "accepted_mutations": 1,
        "cache_hits": 0,
        "execution": {"backend": "host", "jobs": 1},
        "phase_statistics": {
            "schema_version": 1,
            "coverage": "complete",
            "phases": [
                {
                    "phase": "files",
                    "attempts": 1,
                    "no_op": 0,
                    "rejected": 0,
                    "accepted": 1,
                    "superseded": 0,
                    "aborted": 0,
                    "oracle_sample_uses": 1,
                    "oracle_samples": 1,
                    "cache_hits": 0,
                }
            ],
        },
        "holdout_certification": {
            "status": "not_requested",
            "planned_runs": 0,
            "completed_runs": 0,
            "passes": 0,
            "samples": [],
            "artifact_fingerprint": None,
        },
        "events": [],
    }


class ReportValidationTest(unittest.TestCase):
    def test_accepts_complete_report_accounting(self) -> None:
        report = _report()
        self.assertIs(validate_report_document(report), report)

    def test_rejects_phase_accounting_drift(self) -> None:
        report = _report()
        report["phase_statistics"]["phases"][0]["accepted"] = 0
        with self.assertRaisesRegex(ReportValidationError, "attempts accounting"):
            validate_report_document(report)

    def test_rejects_unsupported_schema(self) -> None:
        report = _report()
        report["schema_version"] = 99
        with self.assertRaisesRegex(ReportValidationError, "unsupported"):
            validate_report_document(report)

    def test_validates_certified_payload_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "reduced"
            metadata = root / "reduced.repomin"
            payload.mkdir()
            metadata.mkdir()
            (payload / "required.txt").write_text("keep\n", encoding="utf-8")
            report = _report()
            report["holdout_certification"] = {
                "status": "certified",
                "planned_runs": 1,
                "completed_runs": 1,
                "passes": 1,
                "samples": [{"index": 0, "accepted": True}],
                "artifact_fingerprint": _tree_digest(payload, set()),
            }
            report_path = metadata / "report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            validate_report_file(report_path, payload)
            (payload / "required.txt").write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(ReportValidationError, "fingerprint"):
                validate_report_file(report_path, payload)

    def test_cli_validate_reports_machine_readable_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            report_path.write_text(json.dumps(_report()), encoding="utf-8")
            from contextlib import redirect_stdout
            from io import StringIO

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["report", "validate", str(report_path), "--json"])
            self.assertEqual(0, exit_code)
            self.assertTrue(json.loads(output.getvalue())["valid"])


if __name__ == "__main__":
    unittest.main()
