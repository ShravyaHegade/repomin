import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from repomin.go_manifest import GoManifestReducer, _discover_targets, _remove_target
from repomin.model import FailureSpec, ReductionStats
from repomin.oracle import CommandRunner, FailureOracle
from repomin.session import ReductionSession


GO_MOD = """\
module example.com/fixture

go 1.22

require (
    example.com/required v1.0.0
    example.com/unused v1.0.0 // indirect
)

require example.com/single v1.0.0

replace (
    example.com/required v1.0.0 => ./required
    example.com/unused v1.0.0 => ./unused
)

exclude example.com/excluded v1.0.0
retract [v1.1.0, v1.2.0]

// go 1.99 in a comment is not a target
"""

GO_WORK = """\
go 1.22

use (
    ./app
    ./unused
)

replace (
    example.com/required v1.0.0 => ./required
)

toolchain go1.23.0
"""


class GoManifestReducerTest(unittest.TestCase):
    def test_discovers_directives_and_ignores_module_go_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "go.mod").write_text(GO_MOD, encoding="utf-8")
            targets = _discover_targets(root)
            labels = {target.label for target in targets}
            categories = {target.label: target.category for target in targets}
            self.assertIn("example.com/required", labels)
            self.assertIn("example.com/unused", labels)
            self.assertIn("example.com/single", labels)
            self.assertIn("example.com/excluded v1.0.0", labels)
            self.assertIn("[v1.1.0, v1.2.0]", labels)
            self.assertEqual("require", categories["example.com/required"])
            self.assertEqual("replace", categories["example.com/required v1.0.0 => ./required"])
            self.assertFalse(any(label in {"example.com/fixture", "1.22"} for label in labels))

    def test_block_and_single_line_removals_keep_directives_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "go.mod"
            for label in ("example.com/required", "example.com/unused", "example.com/single"):
                path.write_text(GO_MOD, encoding="utf-8")
                target = next(item for item in _discover_targets(root) if item.label == label)
                self.assertTrue(_remove_target(root, target), label)
                text = path.read_text(encoding="utf-8")
                self.assertIn("module example.com/fixture", text)
                self.assertNotIn("go 1.22", text[text.find("//"):])

    def test_unclosed_block_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "go.mod").write_text(
                "module example.com/broken\n\nrequire (\n example.com/a v1.0.0\n",
                encoding="utf-8",
            )
            self.assertEqual([], _discover_targets(root))

            closed_dir = root / "closed"
            closed_dir.mkdir()
            closed_with_comment = closed_dir / "go.mod"
            closed_with_comment.write_text(
                "module example.com/closed\n\n"
                "require (\n"
                " example.com/a v1.0.0\n"
                ") // close\n",
                encoding="utf-8",
            )
            self.assertEqual(
                ["example.com/a"],
                [item.label for item in _discover_targets(root) if item.path == Path("closed/go.mod")],
            )

    def test_stale_hash_rejects_without_modifying_go_mod(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "go.mod"
            path.write_text(GO_MOD, encoding="utf-8")
            target = next(item for item in _discover_targets(root) if item.label == "example.com/unused")
            shifted = "// shifted\n" + GO_MOD
            path.write_text(shifted, encoding="utf-8")
            self.assertFalse(_remove_target(root, target))
            self.assertEqual(shifted, path.read_text(encoding="utf-8"))

    def test_discovers_workspace_use_replace_and_ignores_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "go.work"
            path.write_text(GO_WORK, encoding="utf-8")
            targets = _discover_targets(root)
            self.assertEqual(
                ["./app", "./unused", "example.com/required v1.0.0 => ./required"],
                [target.label for target in targets],
            )
            self.assertEqual(
                ["use", "use", "replace"],
                [target.category for target in targets],
            )
            self.assertNotIn("1.22", {target.label for target in targets})
            self.assertNotIn("go1.23.0", {target.label for target in targets})
            self.assertTrue(
                all(target.path == Path("go.work") for target in targets)
            )

    def test_discovers_single_line_workspace_use(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "go.work").write_text(
                "go 1.22\nuse ./app\n", encoding="utf-8"
            )
            targets = _discover_targets(root)
            self.assertEqual(["./app"], [target.label for target in targets])
            self.assertEqual(["use"], [target.category for target in targets])

    def test_workspace_use_and_replace_removals_remain_go_parseable(self) -> None:
        go = shutil.which("go")
        if go is None:
            self.skipTest("go toolchain is not installed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app").mkdir()
            (root / "unused").mkdir()
            (root / "app" / "go.mod").write_text(
                "module example.com/app\ngo 1.22\n", encoding="utf-8"
            )
            (root / "unused" / "go.mod").write_text(
                "module example.com/unused\ngo 1.22\n", encoding="utf-8"
            )
            path = root / "go.work"
            path.write_text(GO_WORK, encoding="utf-8")
            for label in ("./app", "./unused", "example.com/required v1.0.0 => ./required"):
                target = next(item for item in _discover_targets(root) if item.label == label)
                self.assertTrue(_remove_target(root, target), label)
                result = subprocess.run(
                    [go, "work", "edit", "-json"],
                    cwd=root,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("toolchain go1.23.0", path.read_text(encoding="utf-8"))

    def test_workspace_stale_hash_rejects_without_modifying_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "go.work"
            path.write_text(GO_WORK, encoding="utf-8")
            target = next(item for item in _discover_targets(root) if item.label == "./app")
            shifted = "// shifted\n" + GO_WORK
            path.write_text(shifted, encoding="utf-8")
            self.assertFalse(_remove_target(root, target))
            self.assertEqual(shifted, path.read_text(encoding="utf-8"))

    def test_workspace_unclosed_block_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "go.work").write_text(
                "go 1.22\n\nuse (\n    ./app\n", encoding="utf-8"
            )
            self.assertEqual([], _discover_targets(root))

    def test_workspace_only_manifest_makes_reducer_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "go.work").write_text(GO_WORK, encoding="utf-8")
            session = ReductionSession(
                root,
                FailureOracle(
                    CommandRunner("python3 -c 'raise SystemExit(1)'", timeout_seconds=5),
                    FailureSpec(None, exit_code=1),
                ),
                ReductionStats(source_files=1, source_bytes=0),
            )
            try:
                self.assertTrue(GoManifestReducer(session).is_applicable())
            finally:
                session.close()

    def test_reducer_preserves_required_module(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "go.mod").write_text(GO_MOD, encoding="utf-8")
            (source / "reproduce.py").write_text(
                "text = open('go.mod', encoding='utf-8').read()\n"
                "if 'example.com/required' not in text:\n"
                "    raise SystemExit(2)\n"
                "print('ORIGINAL_FAILURE')\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            session = ReductionSession(
                source,
                FailureOracle(
                    CommandRunner("python3 reproduce.py", timeout_seconds=5),
                    FailureSpec("ORIGINAL_FAILURE"),
                ),
                ReductionStats(source_files=2, source_bytes=0),
            )
            try:
                session.verify_baseline(1)
                reducer = GoManifestReducer(session)
                self.assertTrue(reducer.is_applicable())
                self.assertTrue(reducer.reduce())
                reduced = (session.current / "go.mod").read_text(encoding="utf-8")
                self.assertIn("example.com/required", reduced)
                self.assertNotIn("example.com/unused v1.0.0 // indirect", reduced)
                self.assertTrue(session.oracle.accepts(session.run_current()))
            finally:
                session.close()


if __name__ == "__main__":
    unittest.main()
