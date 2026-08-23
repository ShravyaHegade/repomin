import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from repomin.maven import MavenReducer, PomTarget, _discover_targets, _remove_targets
from repomin.model import FailureSpec, ReductionStats
from repomin.oracle import CommandRunner, FailureOracle
from repomin.session import ReductionSession


POM = """\
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>dev.repomin</groupId>
  <artifactId>fixture</artifactId>
  <version>1.0-SNAPSHOT</version>
  <modules>
    <module>required</module>
    <module>unused</module>
  </modules>
  <properties>
    <required.flag>true</required.flag>
    <unused.flag>remove-me</unused.flag>
  </properties>
  <dependencies>
    <dependency>
      <groupId>dev.repomin</groupId>
      <artifactId>needed</artifactId>
    </dependency>
    <dependency>
      <groupId>dev.repomin</groupId>
      <artifactId>unused</artifactId>
    </dependency>
  </dependencies>
  <build>
    <plugins>
      <plugin>
        <groupId>dev.repomin</groupId>
        <artifactId>unused-plugin</artifactId>
      </plugin>
    </plugins>
  </build>
</project>
"""

REPRODUCE = """\
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

root = ET.parse("pom.xml").getroot()
names = [element.tag.rsplit("}", 1)[-1] for element in root.iter()]
text = Path("pom.xml").read_text(encoding="utf-8")
required = ["<module>required</module>", "<artifactId>needed</artifactId>", "required.flag"]
if not all(item in text for item in required):
    print("DIFFERENT_FAILURE", file=sys.stderr)
    raise SystemExit(2)
print("ORIGINAL_FAILURE: NoSuchMethodError", file=sys.stderr)
raise SystemExit(1)
"""


class MavenReducerTest(unittest.TestCase):
    def test_cross_pom_batch_is_unchanged_when_a_target_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pom_paths = []
            for name in ("first", "second"):
                pom_path = root / name / "pom.xml"
                pom_path.parent.mkdir()
                pom_path.write_text(POM, encoding="utf-8")
                pom_paths.append(pom_path)

            originals = {path: path.read_bytes() for path in pom_paths}
            targets = [
                target
                for target in _discover_targets(root)
                if target.category == "module" and target.key == ("unused",)
            ]
            self.assertEqual(
                [Path("first/pom.xml"), Path("second/pom.xml")],
                [target.pom for target in targets],
            )
            stale = PomTarget(
                pom=targets[1].pom,
                category=targets[1].category,
                key=("no-longer-present",),
                ordinal=targets[1].ordinal,
                label=targets[1].label,
            )

            self.assertFalse(_remove_targets(root, (targets[0], stale)))
            self.assertEqual(
                originals,
                {path: path.read_bytes() for path in pom_paths},
            )

    def test_cross_pom_batch_rolls_back_when_second_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pom_paths = []
            for name in ("first", "second"):
                pom_path = root / name / "pom.xml"
                pom_path.parent.mkdir()
                pom_path.write_text(POM, encoding="utf-8")
                pom_paths.append(pom_path)

            originals = {path: path.read_bytes() for path in pom_paths}
            targets = [
                target
                for target in _discover_targets(root)
                if target.category == "module" and target.key == ("unused",)
            ]
            self.assertEqual(2, len(targets))
            original_write_bytes = Path.write_bytes
            write_paths = []
            failure_injected = False

            def flaky_write_bytes(path: Path, data: bytes) -> int:
                nonlocal failure_injected
                write_paths.append(path)
                if path == pom_paths[1] and not failure_injected:
                    failure_injected = True
                    raise OSError("injected second-POM write failure")
                return original_write_bytes(path, data)

            with mock.patch.object(Path, "write_bytes", new=flaky_write_bytes):
                self.assertFalse(_remove_targets(root, targets))

            self.assertTrue(failure_injected)
            self.assertEqual(
                [pom_paths[0], pom_paths[1], pom_paths[1], pom_paths[0]],
                write_paths,
            )
            self.assertEqual(
                originals,
                {path: path.read_bytes() for path in pom_paths},
            )

    def test_removes_only_optional_pom_elements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            (source / "pom.xml").write_text(POM, encoding="utf-8")
            (source / "reproduce.py").write_text(REPRODUCE, encoding="utf-8")

            runner = CommandRunner("python3 reproduce.py", timeout_seconds=5)
            oracle = FailureOracle(runner, FailureSpec("ORIGINAL_FAILURE"))
            stats = ReductionStats(source_files=2, source_bytes=0)
            session = ReductionSession(source, oracle, stats)
            try:
                oracle.verify_baseline(session.current, repeat=1)
                reducer = MavenReducer(session)
                self.assertTrue(reducer.is_applicable())
                reducer.reduce()

                pom_text = (session.current / "pom.xml").read_text(encoding="utf-8")
                ET.fromstring(pom_text)
                self.assertIn("<module>required</module>", pom_text)
                self.assertIn("<artifactId>needed</artifactId>", pom_text)
                self.assertIn("required.flag", pom_text)
                self.assertNotIn("<module>unused</module>", pom_text)
                self.assertNotIn("<artifactId>unused</artifactId>", pom_text)
                self.assertNotIn("unused.flag", pom_text)
                self.assertNotIn("unused-plugin", pom_text)
                self.assertTrue(oracle.accepts(session.run_current()))
            finally:
                session.close()


if __name__ == "__main__":
    unittest.main()
