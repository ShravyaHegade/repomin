import tempfile
import unittest
from pathlib import Path

from repomin.model import FailureSpec, ReductionStats
from repomin.oracle import CommandRunner, FailureOracle
from repomin.python_manifest import (
    PythonManifestReducer,
    _discover_targets,
    _remove_target,
)
from repomin.session import ReductionSession


PYPROJECT = '''\
# A comment containing dependencies = ["comment-only"]
[project]
name = "fixture"
description = """Text containing [build-system] and requires=["string-only"]."""
dependencies = [
  "fastapi>=0.100", # required
  "unused-package>=9",
]

[project.optional-dependencies]
test = ["pytest>=8", "coverage>=7"]

[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[tool.poetry.dependencies]
python = "^3.9"
httpx = { version = "^0.27", optional = true }

[tool.poetry.group.dev.dependencies]
ruff = "^0.6"

[tool.pdm.dev-dependencies]
lint = ["mypy", "black"]

[dependency-groups]
docs = ["sphinx", {include-group = "test"}]

[tool.uv]
dev-dependencies = ["hypothesis"]
'''

REQUIREMENTS = '''\
# Root dependency set
-r requirements/base.txt
-c constraints.txt
--index-url https://example.invalid/simple
unused-root==2 \\
    --hash=sha256:aaaaaaaa
'''

BASE_REQUIREMENTS = '''\
required-package==1
unused-base==2
'''

REPRODUCE = '''\
from pathlib import Path
import sys

pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
root = Path("requirements.txt").read_text(encoding="utf-8")
base = Path("requirements/base.txt").read_text(encoding="utf-8")
required = [
    "fastapi>=0.100",
    "-r requirements/base.txt",
    "required-package==1",
]
if not all(value in pyproject + root + base for value in required):
    print("DIFFERENT_FAILURE", file=sys.stderr)
    raise SystemExit(2)
print("ORIGINAL_FAILURE", file=sys.stderr)
raise SystemExit(1)
'''


class PythonManifestReducerTest(unittest.TestCase):
    def test_discovers_supported_toml_dependency_forms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")

            targets = _discover_targets(root)
            labels = {target.label for target in targets}

            self.assertIn("fastapi>=0.100", labels)
            self.assertIn("pytest>=8", labels)
            self.assertIn("setuptools>=68", labels)
            self.assertIn("httpx", labels)
            self.assertIn("ruff", labels)
            self.assertIn("mypy", labels)
            self.assertIn("sphinx", labels)
            self.assertIn('{include-group = "test"}', labels)
            self.assertIn("hypothesis", labels)
            self.assertNotIn("comment-only", labels)
            self.assertNotIn("string-only", labels)

            dotted = root / "pyproject.toml"
            dotted.write_text(
                'project.dependencies = ["pyperf"]\n'
                'tool.poetry.dependencies.httpx = "^0.27"\n',
                encoding="utf-8",
            )
            dotted_labels = {target.label for target in _discover_targets(root)}
            self.assertIn("pyperf", dotted_labels)
            self.assertIn("httpx", dotted_labels)

    def test_follows_local_includes_and_keeps_logical_lines_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requirements = root / "requirements"
            requirements.mkdir()
            (root / "requirements.txt").write_text(REQUIREMENTS, encoding="utf-8")
            (requirements / "base.txt").write_text(
                BASE_REQUIREMENTS, encoding="utf-8"
            )
            (root / "constraints.txt").write_text("urllib3<3\n", encoding="utf-8")

            targets = _discover_targets(root)
            by_label = {target.label: target for target in targets}

            self.assertEqual("include", by_label["-r requirements/base.txt"].category)
            self.assertEqual("constraint", by_label["-c constraints.txt"].category)
            self.assertEqual("option", by_label[
                "--index-url https://example.invalid/simple"
            ].category)
            continued = by_label[
                "unused-root==2 --hash=sha256:aaaaaaaa"
            ]
            self.assertIn("\\\n", (root / continued.path).read_text(encoding="utf-8")[
                continued.start : continued.end
            ])
            self.assertIn("required-package==1", by_label)
            self.assertEqual(Path("requirements/base.txt"), by_label[
                "required-package==1"
            ].path)
            self.assertIn("urllib3<3", by_label)

    def test_reduces_manifests_while_preserving_required_include_chain(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        source = root / "source"
        source.mkdir()
        (source / "requirements").mkdir()
        (source / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
        (source / "requirements.txt").write_text(REQUIREMENTS, encoding="utf-8")
        (source / "requirements" / "base.txt").write_text(
            BASE_REQUIREMENTS, encoding="utf-8"
        )
        (source / "constraints.txt").write_text("urllib3<3\n", encoding="utf-8")
        (source / "reproduce.py").write_text(REPRODUCE, encoding="utf-8")
        session = ReductionSession(
            source,
            FailureOracle(
                CommandRunner("python3 reproduce.py", timeout_seconds=5),
                FailureSpec("ORIGINAL_FAILURE"),
            ),
            ReductionStats(source_files=5, source_bytes=0),
        )
        try:
            session.verify_baseline(1)
            reducer = PythonManifestReducer(session)
            self.assertTrue(reducer.is_applicable())
            reducer.reduce()

            pyproject = (session.current / "pyproject.toml").read_text(
                encoding="utf-8"
            )
            root_requirements = (session.current / "requirements.txt").read_text(
                encoding="utf-8"
            )
            base = (session.current / "requirements" / "base.txt").read_text(
                encoding="utf-8"
            )
            self.assertIn("fastapi>=0.100", pyproject)
            self.assertNotIn("unused-package", pyproject)
            self.assertNotIn("pytest", pyproject)
            self.assertNotIn("setuptools>=68", pyproject)
            self.assertNotIn("httpx", pyproject)
            self.assertIn("-r requirements/base.txt", root_requirements)
            self.assertNotIn("constraints.txt", root_requirements)
            self.assertNotIn("index-url", root_requirements)
            self.assertNotIn("unused-root", root_requirements)
            self.assertIn("required-package==1", base)
            self.assertNotIn("unused-base", base)
            self.assertTrue(session.oracle.accepts(session.run_current()))
        finally:
            session.close()
            temporary.cleanup()

    def test_content_hash_rejects_a_stale_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pyproject = root / "pyproject.toml"
            pyproject.write_text(PYPROJECT, encoding="utf-8")
            target = next(
                item
                for item in _discover_targets(root)
                if item.label == "fastapi>=0.100"
            )
            pyproject.write_text("# shifted\n" + PYPROJECT, encoding="utf-8")

            self.assertFalse(_remove_target(root, target))
            self.assertIn("fastapi>=0.100", pyproject.read_text(encoding="utf-8"))

    def test_requirements_file_alone_makes_adapter_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            (source / "requirements-dev.txt").write_text(
                "pytest>=8\n", encoding="utf-8"
            )
            session = ReductionSession(
                source,
                FailureOracle(
                    CommandRunner(
                        "python3 -c \"import sys; print('FAIL'); sys.exit(1)\"",
                        timeout_seconds=5,
                    ),
                    FailureSpec("FAIL"),
                ),
                ReductionStats(source_files=1, source_bytes=0),
            )
            try:
                self.assertTrue(PythonManifestReducer(session).is_applicable())
            finally:
                session.close()

    def test_discovers_nested_pyproject_in_a_monorepo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = root / "services" / "api"
            service.mkdir(parents=True)
            (service / "pyproject.toml").write_text(
                '[project]\ndependencies = ["fastapi"]\n', encoding="utf-8"
            )

            target = next(
                item for item in _discover_targets(root) if item.label == "fastapi"
            )

            self.assertEqual(Path("services/api/pyproject.toml"), target.path)


if __name__ == "__main__":
    unittest.main()
