import tempfile
import unittest
from pathlib import Path

from repomin.gradle import GradleReducer, _discover_targets, _remove_target
from repomin.model import FailureSpec, ReductionStats
from repomin.oracle import CommandRunner, FailureOracle
from repomin.session import ReductionSession


REPRODUCE = """\
from pathlib import Path
import sys

settings = next(path for path in [Path("settings.gradle"), Path("settings.gradle.kts")] if path.exists())
build = next(path for path in [Path("build.gradle"), Path("build.gradle.kts")] if path.exists())
settings_text = settings.read_text(encoding="utf-8")
build_text = build.read_text(encoding="utf-8")
properties = Path("gradle.properties").read_text(encoding="utf-8")
required = [
    ":app",
    "dev:required:1",
    "required.flag",
]
plugin_ok = "id 'java'" in build_text or "java" in build_text
if not all(value in settings_text + build_text + properties for value in required) or not plugin_ok:
    print("DIFFERENT_FAILURE", file=sys.stderr)
    raise SystemExit(2)
print("ORIGINAL_FAILURE", file=sys.stderr)
raise SystemExit(1)
"""

GROOVY_SETTINGS = """\
rootProject.name = 'fixture'
include ':app', ':unused'
"""

GROOVY_BUILD = '''\
plugins {
    id 'java'
    id 'com.example.unused' version '1.0'
}

// dependencies { implementation 'comment:only:1' }
def sample = "plugins { id 'string.only' }"

dependencies {
    implementation 'dev:required:1'
    implementation('dev:unused:1') {
        transitive = false
    }
}

repositories {
    mavenCentral()
}
'''

KOTLIN_SETTINGS = """\
rootProject.name = "fixture"
include(
    ":app",
    ":unused",
)
"""

KOTLIN_BUILD = '''\
plugins {
    java
    id("com.example.unused")
        version "1.0"
}

val sample = """dependencies { implementation("string:only:1") }"""

dependencies {
    implementation(
        "dev:required:1"
    )
    implementation("dev:unused:1")
}

configurations {
    create("unusedClasspath")
}
'''

PROPERTIES = """\
required.flag=true
unused.flag=first\\
  second
"""


class GradleReducerTest(unittest.TestCase):
    def _reduce(self, settings_name: str, settings: str, build_name: str, build: str):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        source = root / "source"
        source.mkdir()
        (source / settings_name).write_text(settings, encoding="utf-8")
        (source / build_name).write_text(build, encoding="utf-8")
        (source / "gradle.properties").write_text(PROPERTIES, encoding="utf-8")
        (source / "reproduce.py").write_text(REPRODUCE, encoding="utf-8")
        runner = CommandRunner("python3 reproduce.py", timeout_seconds=5)
        oracle = FailureOracle(runner, FailureSpec("ORIGINAL_FAILURE"))
        stats = ReductionStats(source_files=4, source_bytes=0)
        session = ReductionSession(source, oracle, stats)
        session.verify_baseline(1)
        reducer = GradleReducer(session)
        self.assertTrue(reducer.is_applicable())
        reducer.reduce()
        return temporary, session

    def test_reduces_groovy_dsl_without_matching_comments_or_strings(self) -> None:
        temporary, session = self._reduce(
            "settings.gradle", GROOVY_SETTINGS, "build.gradle", GROOVY_BUILD
        )
        try:
            settings = (session.current / "settings.gradle").read_text(encoding="utf-8")
            build = (session.current / "build.gradle").read_text(encoding="utf-8")
            properties = (session.current / "gradle.properties").read_text(
                encoding="utf-8"
            )

            self.assertIn(":app", settings)
            self.assertNotIn(":unused", settings)
            self.assertIn("id 'java'", build)
            self.assertNotIn("com.example.unused", build)
            self.assertNotIn("version '1.0'", build)
            self.assertIn("dev:required:1", build)
            self.assertNotIn("dev:unused:1", build)
            self.assertNotIn("mavenCentral", build)
            self.assertNotIn("repositories", build)
            self.assertIn("comment:only:1", build)
            self.assertIn("string.only", build)
            self.assertIn("required.flag", properties)
            self.assertNotIn("unused.flag", properties)
            self.assertNotIn("second", properties)
            self.assertTrue(session.oracle.accepts(session.run_current()))
        finally:
            session.close()
            temporary.cleanup()

    def test_reduces_kotlin_dsl_and_preserves_multiline_syntax(self) -> None:
        temporary, session = self._reduce(
            "settings.gradle.kts",
            KOTLIN_SETTINGS,
            "build.gradle.kts",
            KOTLIN_BUILD,
        )
        try:
            settings = (session.current / "settings.gradle.kts").read_text(
                encoding="utf-8"
            )
            build = (session.current / "build.gradle.kts").read_text(encoding="utf-8")

            self.assertIn(":app", settings)
            self.assertNotIn(":unused", settings)
            self.assertEqual(settings.count("("), settings.count(")"))
            self.assertIn("java", build)
            self.assertNotIn("com.example.unused", build)
            self.assertNotIn('version "1.0"', build)
            self.assertIn("dev:required:1", build)
            self.assertNotIn("dev:unused:1", build)
            self.assertNotIn("unusedClasspath", build)
            self.assertNotIn("configurations", build)
            self.assertIn("string:only:1", build)
            self.assertEqual(build.count("{"), build.count("}"))
            self.assertTrue(session.oracle.accepts(session.run_current()))
        finally:
            session.close()
            temporary.cleanup()

    def test_content_hash_rejects_a_stale_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "build.gradle"
            build.write_text(GROOVY_BUILD, encoding="utf-8")
            target = next(
                item
                for item in _discover_targets(root)
                if item.category == "dependency"
            )
            build.write_text("// shifted\n" + GROOVY_BUILD, encoding="utf-8")

            self.assertFalse(_remove_target(root, target))
            self.assertIn("dev:required:1", build.read_text(encoding="utf-8"))

    def test_gradle_properties_alone_make_adapter_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            (source / "gradle.properties").write_text(
                "unused.flag=true\n", encoding="utf-8"
            )
            runner = CommandRunner(
                "python3 -c \"import sys; print('ORIGINAL_FAILURE'); sys.exit(1)\"",
                timeout_seconds=5,
            )
            oracle = FailureOracle(runner, FailureSpec("ORIGINAL_FAILURE"))
            session = ReductionSession(
                source,
                oracle,
                ReductionStats(source_files=1, source_bytes=0),
            )
            try:
                self.assertTrue(GradleReducer(session).is_applicable())
            finally:
                session.close()


if __name__ == "__main__":
    unittest.main()
