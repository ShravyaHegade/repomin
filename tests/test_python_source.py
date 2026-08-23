import tempfile
import unittest
from pathlib import Path

from repomin.model import FailureSpec, ReductionStats
from repomin.oracle import CommandRunner, FailureOracle
from repomin.python_source import (
    PythonSourceReducer,
    _discover_targets,
    _remove_target,
)
from repomin.session import ReductionSession


SOURCE = '''\
from pathlib import Path
import json
import sys

title = "中文标题"

def decorator(function):
    return function

@decorator
def unused_decorated():
    return "unused"

class Unused:
    value = "unused"

def fallback_failure():
    raise ValueError("different failure")

def target_failure():
    noise = {"unused": True}
    print("ORIGINAL_FAILURE")
    raise SystemExit(1)

if Path("required.txt").exists():
    target_failure()
else:
    print("DIFFERENT_FAILURE", file=sys.stderr)
    raise SystemExit(2)
'''


class PythonSourceReducerTest(unittest.TestCase):
    def _session(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        source = root / "source"
        source.mkdir()
        (source / "reproduce.py").write_text(SOURCE, encoding="utf-8")
        (source / "required.txt").write_text("keep\n", encoding="utf-8")
        oracle = FailureOracle(
            CommandRunner("python3 reproduce.py", timeout_seconds=5),
            FailureSpec("ORIGINAL_FAILURE"),
        )
        session = ReductionSession(
            source,
            oracle,
            ReductionStats(source_files=2, source_bytes=len(SOURCE)),
        )
        return temporary, session

    def test_reduces_ast_without_matching_strings_or_comments(self) -> None:
        temporary, session = self._session()
        try:
            session.verify_baseline(1)
            changed = PythonSourceReducer(session).reduce()
            reduced = (session.current / "reproduce.py").read_text(encoding="utf-8")

            self.assertTrue(changed)
            self.assertIn("from pathlib import Path", reduced)
            self.assertIn("target_failure", reduced)
            self.assertIn("ORIGINAL_FAILURE", reduced)
            self.assertNotIn("import json", reduced)
            self.assertNotIn("unused_decorated", reduced)
            self.assertNotIn("class Unused", reduced)
            self.assertNotIn("fallback_failure", reduced)
            self.assertNotIn('"unused": True', reduced)
            self.assertTrue(session.oracle.accepts(session.run_current()))
        finally:
            session.close()
            temporary.cleanup()

    def test_decorator_is_included_in_definition_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "fixture.py"
            path.write_text(
                "def mark(fn):\n    return fn\n\n"
                "@mark\ndef unused():\n    return 1\n",
                encoding="utf-8",
            )

            target = next(
                item
                for item in _discover_targets(root)
                if item.label == "unused"
            )
            text = path.read_text(encoding="utf-8")

            self.assertTrue(text[target.start : target.end].lstrip().startswith("@mark"))

    def test_utf8_offsets_and_stale_hash_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "fixture.py"
            path.write_text(
                'title = "中文标题"\n\n'
                "def unused():\n    return 1\n",
                encoding="utf-8",
            )
            target = next(
                item for item in _discover_targets(root) if item.label == "unused"
            )
            self.assertTrue(_remove_target(root, target))
            self.assertEqual('title = "中文标题"\n\n', path.read_text(encoding="utf-8"))

            path.write_text(
                "# shifted\n" + 'title = "中文标题"\n\n'
                "def unused():\n    return 1\n",
                encoding="utf-8",
            )
            self.assertFalse(_remove_target(root, target))

    def test_invalid_python_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")

            self.assertEqual([], _discover_targets(root))

    def test_reducer_is_applicable_to_nested_python_projects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "services" / "api"
            nested.mkdir(parents=True)
            (nested / "main.py").write_text("unused = 1\n", encoding="utf-8")
            session_root = root / "source"
            session_root.mkdir()
            (session_root / "main.py").write_text("unused = 1\n", encoding="utf-8")
            session = ReductionSession(
                session_root,
                FailureOracle(
                    CommandRunner("false", timeout_seconds=1),
                    FailureSpec("failure"),
                ),
                ReductionStats(source_files=1, source_bytes=11),
            )
            try:
                self.assertTrue(PythonSourceReducer(session).is_applicable())
            finally:
                session.close()


if __name__ == "__main__":
    unittest.main()
