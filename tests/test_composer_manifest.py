import json
import tempfile
import unittest
from pathlib import Path

from repomin.composer_manifest import (
    ComposerManifestReducer,
    _discover_targets,
    _remove_target,
)
from repomin.model import FailureSpec, ReductionStats
from repomin.oracle import CommandRunner, FailureOracle
from repomin.session import ReductionSession


COMPOSER_JSON = """\
{
  "name": "vendor/fixture",
  "require": {
    "php": ">=8.1",
    "vendor/required": "^1.0",
    "vendor/unused": "^1.0"
  },
  "require-dev": {
    "vendor/test-unused": "^1.0"
  },
  "replace": {
    "vendor/replaced": "self.version"
  },
  "conflict": {
    "vendor/conflicting": "<2.0"
  },
  "provide": {
    "vendor/provided": "1.0"
  },
  "scripts": {
    "test": "phpunit",
    "unused": "echo unused"
  },
  "repositories": [
    {"type": "path", "url": "required"},
    {"type": "vcs", "url": "unused"}
  ],
  "autoload": {
    "psr-4": {"Vendor\\\\": "src/"}
  },
  "extra": {"keep": true}
}
"""


class ComposerManifestReducerTest(unittest.TestCase):
    def test_discovers_safe_entries_and_ignores_runtime_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "composer.json"
            path.write_text(COMPOSER_JSON, encoding="utf-8")
            targets = _discover_targets(root)
            labels = {target.label for target in targets}
            categories = {target.label: target.category for target in targets}
            self.assertIn("require.vendor/required", labels)
            self.assertIn("require-dev.vendor/test-unused", labels)
            self.assertIn("replace.vendor/replaced", labels)
            self.assertIn("conflict.vendor/conflicting", labels)
            self.assertIn("provide.vendor/provided", labels)
            self.assertIn("scripts.unused", labels)
            self.assertIn("repositories[0]=path", labels)
            self.assertIn("repositories[1]=vcs", labels)
            self.assertEqual("dependency", categories["require.vendor/required"])
            self.assertEqual("repository", categories["repositories[0]=path"])
            self.assertNotIn("autoload.psr-4", labels)
            self.assertNotIn("extra.keep", labels)

    def test_removals_keep_composer_json_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "composer.json"
            for label in (
                "require.php",
                "require.vendor/unused",
                "repositories[1]=vcs",
            ):
                path.write_text(COMPOSER_JSON, encoding="utf-8")
                target = next(item for item in _discover_targets(root) if item.label == label)
                self.assertTrue(_remove_target(root, target), label)
                parsed = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual("vendor/fixture", parsed["name"])

    def test_duplicate_keys_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "composer.json").write_text(
                '{"require":{"vendor/a":"^1"},"require":{"vendor/b":"^1"}}',
                encoding="utf-8",
            )
            self.assertEqual([], _discover_targets(root))

    def test_stale_hash_rejects_without_modifying_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "composer.json"
            path.write_text(COMPOSER_JSON, encoding="utf-8")
            target = next(
                item
                for item in _discover_targets(root)
                if item.label == "require.vendor/unused"
            )
            shifted = "\n" + COMPOSER_JSON
            path.write_text(shifted, encoding="utf-8")
            self.assertFalse(_remove_target(root, target))
            self.assertEqual(shifted, path.read_text(encoding="utf-8"))

    def test_composer_only_manifest_makes_adapter_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "composer.json").write_text(COMPOSER_JSON, encoding="utf-8")
            session = ReductionSession(
                root,
                FailureOracle(
                    CommandRunner("python3 -c 'raise SystemExit(1)'", timeout_seconds=5),
                    FailureSpec(None, exit_code=1),
                ),
                ReductionStats(source_files=1, source_bytes=0),
            )
            try:
                self.assertTrue(ComposerManifestReducer(session).is_applicable())
            finally:
                session.close()

    def test_reducer_preserves_required_dependency_and_autoload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            (source / "composer.json").write_text(COMPOSER_JSON, encoding="utf-8")
            (source / "reproduce.py").write_text(
                "import json\n"
                "from pathlib import Path\n"
                "manifest = json.loads(Path('composer.json').read_text())\n"
                "if 'vendor/required' not in manifest['require']:\n"
                "    raise SystemExit(2)\n"
                "if 'autoload' not in manifest or 'psr-4' not in manifest['autoload']:\n"
                "    raise SystemExit(3)\n"
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
                reducer = ComposerManifestReducer(session)
                self.assertTrue(reducer.is_applicable())
                self.assertTrue(reducer.reduce())
                reduced = json.loads(
                    (session.current / "composer.json").read_text(encoding="utf-8")
                )
                self.assertIn("vendor/required", reduced["require"])
                self.assertNotIn("vendor/unused", reduced["require"])
                self.assertIn("autoload", reduced)
                self.assertTrue(session.oracle.accepts(session.run_current()))
            finally:
                session.close()


if __name__ == "__main__":
    unittest.main()
