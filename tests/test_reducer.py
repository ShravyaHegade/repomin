import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from repomin.model import FailureSpec, ReductionStats, RunResult
from repomin.oracle import CommandRunner, FailureOracle
from repomin.reducer import FileReducer
from repomin.session import ReductionSession


REPRODUCE_SCRIPT = """\
from pathlib import Path
import sys

if not Path("needed.txt").exists():
    print("DIFFERENT_FAILURE: missing input", file=sys.stderr)
    raise SystemExit(2)

print("ORIGINAL_FAILURE: demo.Target.missing", file=sys.stderr)
raise SystemExit(1)
"""


class FileReducerTest(unittest.TestCase):
    def test_file_candidates_are_sorted_before_minimization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory)
            for name in ("z.txt", "a.txt", "nested/m.txt"):
                path = current / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name, encoding="utf-8")
            session = SimpleNamespace(
                current=current,
                stats=SimpleNamespace(accepted=0),
            )
            reducer = FileReducer(session)
            captured = []
            original_rglob = Path.rglob

            def reversed_rglob(path, pattern):
                return iter(reversed(list(original_rglob(path, pattern))))

            with mock.patch.object(reducer, "_reduce_directories"), mock.patch.object(
                reducer,
                "_minimize",
                side_effect=lambda items, _kind: captured.extend(items),
            ), mock.patch.object(Path, "rglob", new=reversed_rglob):
                reducer._reduce()

            self.assertEqual(
                [Path("a.txt"), Path("nested/m.txt"), Path("z.txt")],
                captured,
            )

    def test_file_reducer_tries_joint_deletion_before_single_files(self) -> None:
        class JointDeletionRunner:
            def run(self, cwd: Path) -> RunResult:
                existing = sum((cwd / name).exists() for name in ("a.txt", "b.txt"))
                output = "ORIGINAL_FAILURE" if existing in {0, 2} else "OTHER"
                return RunResult(1, output, "", 0.0)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            (source / "a.txt").write_text("a\n", encoding="utf-8")
            (source / "b.txt").write_text("b\n", encoding="utf-8")
            oracle = FailureOracle(
                JointDeletionRunner(),
                FailureSpec("ORIGINAL_FAILURE"),
            )
            session = ReductionSession(
                source,
                oracle,
                ReductionStats(source_files=2, source_bytes=4),
            )
            try:
                session.verify_baseline(1)

                FileReducer(session).reduce()

                self.assertEqual([], list(session.current.iterdir()))
                self.assertEqual(1, session.stats.attempts)
            finally:
                session.close()

    def test_rechecks_directories_after_file_reduction(self) -> None:
        class NonMonotonicRunner:
            def run(self, cwd: Path) -> RunResult:
                a_exists = (cwd / "d" / "a.txt").exists()
                b_exists = (cwd / "b.txt").exists()
                output = "ORIGINAL_FAILURE" if a_exists or not b_exists else "OTHER"
                return RunResult(1, output, "", 0.0)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            (source / "d").mkdir(parents=True)
            (source / "d" / "a.txt").write_text("a\n", encoding="utf-8")
            (source / "b.txt").write_text("b\n", encoding="utf-8")
            oracle = FailureOracle(
                NonMonotonicRunner(),
                FailureSpec("ORIGINAL_FAILURE"),
            )
            session = ReductionSession(
                source,
                oracle,
                ReductionStats(source_files=2, source_bytes=4),
            )
            try:
                session.verify_baseline(1)

                FileReducer(session).reduce()

                self.assertEqual([], list(session.current.iterdir()))
                self.assertTrue(oracle.accepts(session.run_current()))
            finally:
                session.close()

    def test_removes_noise_without_modifying_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "reproduce.py").write_text(REPRODUCE_SCRIPT, encoding="utf-8")
            (source / "needed.txt").write_text("keep\n", encoding="utf-8")
            noise = source / "docs"
            noise.mkdir()
            for index in range(6):
                (noise / ("noise-%d.txt" % index)).write_text("unused\n", encoding="utf-8")

            runner = CommandRunner("python3 reproduce.py", timeout_seconds=5)
            oracle = FailureOracle(runner, FailureSpec("ORIGINAL_FAILURE"))
            stats = ReductionStats(source_files=8, source_bytes=0)
            session = ReductionSession(source, oracle, stats)
            try:
                oracle.verify_baseline(session.current, repeat=2)
                FileReducer(session).reduce()
                final_run = session.run_current()

                self.assertTrue(oracle.accepts(final_run))
                self.assertTrue((session.current / "reproduce.py").exists())
                self.assertTrue((session.current / "needed.txt").exists())
                self.assertFalse((session.current / "docs").exists())
                self.assertTrue((source / "docs" / "noise-0.txt").exists())
                self.assertGreater(stats.accepted, 0)
            finally:
                session.close()

    def test_generated_artifact_cannot_replace_required_source(self) -> None:
        script = """\
from pathlib import Path
import sys

source = Path("src/Trigger.java")
cached = Path("target/Trigger.class")
if source.exists():
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text("compiled", encoding="utf-8")
    print("ORIGINAL_FAILURE", file=sys.stderr)
    raise SystemExit(1)
if cached.exists():
    print("ORIGINAL_FAILURE", file=sys.stderr)
    raise SystemExit(1)
print("DIFFERENT_FAILURE: source and cached class are missing", file=sys.stderr)
raise SystemExit(2)
"""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            (source / "src").mkdir(parents=True)
            (source / "src" / "Trigger.java").write_text("required\n", encoding="utf-8")
            (source / "reproduce.py").write_text(script, encoding="utf-8")

            runner = CommandRunner("python3 reproduce.py", timeout_seconds=5)
            oracle = FailureOracle(runner, FailureSpec("ORIGINAL_FAILURE"))
            stats = ReductionStats(source_files=2, source_bytes=0)
            session = ReductionSession(source, oracle, stats)
            try:
                oracle.verify_baseline(
                    session.current,
                    repeat=2,
                    reset=session.clean_generated,
                )
                FileReducer(session).reduce()

                self.assertTrue((session.current / "src" / "Trigger.java").exists())
                self.assertFalse((session.current / "target").exists())
                self.assertTrue(oracle.accepts(session.run_current()))
            finally:
                session.close()

    def test_file_reducer_preserves_kept_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            (source / "reproduce.py").write_text(REPRODUCE_SCRIPT, encoding="utf-8")
            (source / "needed.txt").write_text("required\n", encoding="utf-8")
            (source / "unused.txt").write_text("unused\n", encoding="utf-8")
            (source / "LICENSE").write_text("license\n", encoding="utf-8")

            runner = CommandRunner("python3 reproduce.py", timeout_seconds=5)
            oracle = FailureOracle(runner, FailureSpec("ORIGINAL_FAILURE"))
            stats = ReductionStats(source_files=4, source_bytes=0)
            session = ReductionSession(
                source,
                oracle,
                stats,
                keep_paths=["LICENSE"],
            )
            try:
                session.verify_baseline(1)
                FileReducer(session).reduce()

                self.assertTrue((session.current / "reproduce.py").exists())
                self.assertTrue((session.current / "needed.txt").exists())
                self.assertTrue((session.current / "LICENSE").exists())
                self.assertFalse((session.current / "unused.txt").exists())
                self.assertEqual(["LICENSE"], stats.keep_paths)
            finally:
                session.close()


if __name__ == "__main__":
    unittest.main()
