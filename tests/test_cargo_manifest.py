import tempfile
import unittest
from pathlib import Path

from repomin.cargo_manifest import (
    CargoManifestReducer,
    _discover_targets,
    _remove_target,
)
from repomin.model import FailureSpec, ReductionStats
from repomin.oracle import CommandRunner, FailureOracle
from repomin.session import ReductionSession


CARGO_TOML = """\
[package]
name = "fixture"
version = "0.1.0"
edition = "2021"

[dependencies]
required-lib = { path = "required-lib" }
unused-lib = { path = "unused-lib" }

[dev-dependencies]
unused-test = "1"

[workspace]
members = ["app", "unused"]
exclude = ["ignored"]

[target.'cfg(unix)'.dependencies]
target-unused = "1"

[features]
default = []
"""


class CargoManifestReducerTest(unittest.TestCase):
    def test_discovers_dependency_target_workspace_and_target_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "Cargo.toml"
            path.write_text(CARGO_TOML, encoding="utf-8")
            targets = _discover_targets(root)
            labels = {target.label for target in targets}
            categories = {target.label: target.category for target in targets}
            self.assertIn("required-lib", labels)
            self.assertIn("unused-lib", labels)
            self.assertIn("unused-test", labels)
            self.assertIn("app", labels)
            self.assertIn("unused", labels)
            self.assertIn("ignored", labels)
            self.assertIn("target-unused", labels)
            self.assertEqual("dependency", categories["required-lib"])
            self.assertEqual("workspace-member", categories["app"])
            self.assertEqual("workspace-exclude", categories["ignored"])
            self.assertFalse(any(label == "default" for label in labels))

    def test_removal_ranges_keep_toml_valid_for_first_and_last_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "Cargo.toml"
            for label in ("required-lib", "unused-lib", "app", "unused"):
                path.write_text(CARGO_TOML, encoding="utf-8")
                target = next(item for item in _discover_targets(root) if item.label == label)
                self.assertTrue(_remove_target(root, target), label)
                text = path.read_text(encoding="utf-8")
                self.assertIn("[package]", text)

    def test_stale_hash_rejects_without_modifying_cargo_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "Cargo.toml"
            path.write_text(CARGO_TOML, encoding="utf-8")
            target = next(item for item in _discover_targets(root) if item.label == "unused-lib")
            shifted = "# shifted\n" + CARGO_TOML
            path.write_text(shifted, encoding="utf-8")
            self.assertFalse(_remove_target(root, target))
            self.assertEqual(shifted, path.read_text(encoding="utf-8"))

    def test_reducer_preserves_required_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "Cargo.toml").write_text(CARGO_TOML, encoding="utf-8")
            (source / "reproduce.py").write_text(
                "text = open('Cargo.toml', encoding='utf-8').read()\n"
                "if 'required-lib' not in text:\n"
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
                reducer = CargoManifestReducer(session)
                self.assertTrue(reducer.is_applicable())
                self.assertTrue(reducer.reduce())
                reduced = (session.current / "Cargo.toml").read_text(encoding="utf-8")
                self.assertIn("required-lib", reduced)
                self.assertNotIn("unused-lib", reduced)
                self.assertNotIn("unused-test", reduced)
                self.assertTrue(session.oracle.accepts(session.run_current()))
            finally:
                session.close()


if __name__ == "__main__":
    unittest.main()
