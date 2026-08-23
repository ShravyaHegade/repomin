import json
import tempfile
import unittest
from pathlib import Path

from repomin.model import FailureSpec, ReductionStats
from repomin.node_manifest import (
    NodeManifestReducer,
    _discover_targets,
    _remove_target,
)
from repomin.oracle import CommandRunner, FailureOracle
from repomin.session import ReductionSession


PACKAGE_JSON = """\
{
  "name": "fixture",
  "dependencies": {
    "required-lib": "1.0.0",
    "unused-lib": "2.0.0"
  },
  "devDependencies": {
    "unused-test": "3.0.0"
  },
  "scripts": {
    "test": "node reproduce.js",
    "unused": "node unused.js"
  },
  "workspaces": [
    "packages/required",
    "packages/unused"
  ],
  "files": ["dist", "README.md"],
  "overrides": {
    "nested-unused": "1.0.0"
  },
  "engines": {"node": ">=18"}
}
"""


class NodeManifestReducerTest(unittest.TestCase):
    def test_discovers_supported_package_entries_without_touching_engines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "package.json"
            path.write_text(PACKAGE_JSON, encoding="utf-8")

            targets = _discover_targets(root)
            labels = {target.label for target in targets}
            categories = {target.label: target.category for target in targets}

            self.assertIn("dependencies.required-lib", labels)
            self.assertIn("dependencies.unused-lib", labels)
            self.assertIn("devDependencies.unused-test", labels)
            self.assertIn("scripts.unused", labels)
            self.assertIn("workspaces[1]=packages/unused", labels)
            self.assertIn("files[0]=dist", labels)
            self.assertIn("overrides.nested-unused", labels)
            self.assertEqual("dependency", categories["dependencies.required-lib"])
            self.assertEqual("script", categories["scripts.unused"])
            self.assertEqual("workspace", categories["workspaces[1]=packages/unused"])
            self.assertFalse(any("engines" in label for label in labels))

    def test_removal_ranges_keep_json_valid_for_first_last_and_array_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "package.json"
            path.write_text(PACKAGE_JSON, encoding="utf-8")

            for label in (
                "dependencies.required-lib",
                "dependencies.unused-lib",
                "workspaces[0]=packages/required",
                "workspaces[1]=packages/unused",
                "files[1]=README.md",
            ):
                path.write_text(PACKAGE_JSON, encoding="utf-8")
                target = next(item for item in _discover_targets(root) if item.label == label)
                self.assertTrue(_remove_target(root, target), label)
                parsed = json.loads(path.read_text(encoding="utf-8"))
                if label == "dependencies.required-lib":
                    self.assertNotIn("required-lib", parsed["dependencies"])
                elif label == "dependencies.unused-lib":
                    self.assertNotIn("unused-lib", parsed["dependencies"])
                elif label.startswith("workspaces"):
                    self.assertEqual(1, len(parsed["workspaces"]))
                else:
                    self.assertEqual(["dist"], parsed["files"])

    def test_invalid_or_duplicate_json_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = root / "invalid" / "package.json"
            invalid.parent.mkdir()
            invalid.write_text('{"dependencies": [}', encoding="utf-8")
            duplicate = root / "duplicate" / "package.json"
            duplicate.parent.mkdir()
            duplicate.write_text(
                '{"dependencies": {"a": "1"}, "dependencies": {"b": "2"}}',
                encoding="utf-8",
            )
            self.assertEqual([], _discover_targets(root))

            nonstandard = root / "nonstandard" / "package.json"
            nonstandard.parent.mkdir()
            nonstandard.write_text(
                '{"dependencies": {"a": NaN}}', encoding="utf-8"
            )
            self.assertEqual([], _discover_targets(root))

    def test_reducer_preserves_required_package_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "package.json").write_text(PACKAGE_JSON, encoding="utf-8")
            (source / "reproduce.py").write_text(
                "import json\n"
                "package = json.load(open('package.json', encoding='utf-8'))\n"
                "if package['dependencies'].get('required-lib') != '1.0.0':\n"
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
                reducer = NodeManifestReducer(session)
                self.assertTrue(reducer.is_applicable())
                self.assertTrue(reducer.reduce())
                reduced = json.loads(
                    (session.current / "package.json").read_text(encoding="utf-8")
                )
                self.assertEqual("1.0.0", reduced["dependencies"]["required-lib"])
                self.assertNotIn("unused-lib", reduced["dependencies"])
                self.assertNotIn("unused-test", reduced["devDependencies"])
                self.assertNotIn("unused", reduced["scripts"])
                self.assertTrue(session.oracle.accepts(session.run_current()))
            finally:
                session.close()

    def test_stale_content_hash_rejects_without_modifying_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "package.json"
            path.write_text(PACKAGE_JSON, encoding="utf-8")
            target = next(
                item
                for item in _discover_targets(root)
                if item.label == "dependencies.unused-lib"
            )
            shifted = "\n" + PACKAGE_JSON
            path.write_text(shifted, encoding="utf-8")
            self.assertFalse(_remove_target(root, target))
            self.assertEqual(shifted, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
