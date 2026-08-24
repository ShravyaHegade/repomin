import tempfile
import unittest
from pathlib import Path

from repomin.model import FailureSpec, ReductionStats
from repomin.oracle import CommandRunner, FailureOracle
from repomin.pipenv_manifest import (
    PipenvManifestReducer,
    _discover_targets,
    _remove_target,
)
from repomin.session import ReductionSession


PIPFILE = '''\
[[source]]
url = "https://pypi.org/simple"
verify_ssl = true
name = "pypi"

[packages]
required-package = "*"
unused-package = "*"

[dev-packages]
unused-test = "*"

[requires]
python_version = "3.11"
'''


class PipenvManifestReducerTest(unittest.TestCase):
    def test_discovers_only_supported_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "Pipfile"
            path.write_text(PIPFILE, encoding="utf-8")
            targets = _discover_targets(root)
            self.assertEqual(
                {"required-package", "unused-package", "unused-test", "python_version"},
                {target.label for target in targets},
            )
            self.assertEqual(
                {"dependency", "dev-dependency", "option"},
                {target.category for target in targets},
            )
            self.assertFalse(any(target.label in {"url", "verify_ssl", "name"} for target in targets))

    def test_removal_ranges_keep_source_and_table_headers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "Pipfile"
            for label in ("required-package", "unused-package", "unused-test", "python_version"):
                path.write_text(PIPFILE, encoding="utf-8")
                target = next(item for item in _discover_targets(root) if item.label == label)
                self.assertTrue(_remove_target(root, target), label)
                text = path.read_text(encoding="utf-8")
                self.assertIn("[[source]]", text)
                self.assertIn("[packages]", text)
                self.assertIn("[dev-packages]", text)
                self.assertIn("[requires]", text)

    def test_stale_hash_rejects_without_modifying_pipfile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "Pipfile"
            path.write_text(PIPFILE, encoding="utf-8")
            target = next(item for item in _discover_targets(root) if item.label == "unused-package")
            shifted = "# shifted\n" + PIPFILE
            path.write_text(shifted, encoding="utf-8")
            self.assertFalse(_remove_target(root, target))
            self.assertEqual(shifted, path.read_text(encoding="utf-8"))

    def test_reducer_preserves_required_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "Pipfile").write_text(PIPFILE, encoding="utf-8")
            (source / "reproduce.py").write_text(
                "from pathlib import Path\n"
                "text = Path('Pipfile').read_text(encoding='utf-8')\n"
                "if 'required-package' not in text:\n"
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
                reducer = PipenvManifestReducer(session)
                self.assertTrue(reducer.is_applicable())
                self.assertTrue(reducer.reduce())
                reduced = (session.current / "Pipfile").read_text(encoding="utf-8")
                self.assertIn("required-package", reduced)
                self.assertNotIn("unused-package", reduced)
                self.assertNotIn("unused-test", reduced)
                self.assertNotIn("python_version", reduced)
                self.assertTrue(session.oracle.accepts(session.run_current()))
            finally:
                session.close()


if __name__ == "__main__":
    unittest.main()
