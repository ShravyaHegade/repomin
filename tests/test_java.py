import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from repomin.java import (
    JavaChangeSet,
    JavaReducer,
    JavaReducerError,
    JavaTarget,
    _JavaStructureAnalyzer,
    _apply_candidate,
    _apply_target,
    _combine_java_candidates,
    _ordered_candidates,
    _parse_targets,
    prepare_java_analysis_classpath,
)
from repomin.model import FailureSpec, ReductionStats
from repomin.oracle import CommandRunner, FailureOracle
from repomin.session import ReductionSession


JAVA_SOURCE = """\
package demo;

import java.util.List;

public final class Trigger {
    private static final int UNUSED_FIELD = 42;

    private static void unusedMethod() {
        System.out.println("unused");
    }

    public static void main(String[] args) {
        int noise = 1;
        if (noise > 0) {
            noise++;
        }
        throw new NoSuchMethodError("demo.Target.missing()");
    }
}
"""

DEEP_JAVA_SOURCE = """\
package demo;

@SuppressWarnings("all")
public final class DeepTrigger {
    @Deprecated
    private static void reproduce(@Deprecated Object unused) {
        throw new NoSuchMethodError(
                true ? "DEEP_ORIGINAL_FAILURE" : "unreachable");
    }

    public static void main(String[] args) throws Exception {
        for (java.lang.reflect.Method method : DeepTrigger.class.getDeclaredMethods()) {
            if (method.getName().equals("reproduce")) {
                method.invoke(null, new Object[method.getParameterCount()]);
            }
        }
    }
}
"""

COORDINATED_HELPER_SOURCE = """\
package demo;

final class Parts {
    static String keep(String value, String unused) {
        return value;
    }
}
"""

COORDINATED_TRIGGER_SOURCE = """\
package demo;

public final class CoordinatedTrigger {
    public static void main(String[] args) {
        String left = Parts.keep("COORDINATED_", "noise-one");
        String right = Parts.keep("FAILURE", "noise-two");
        throw new NoSuchMethodError(left + right);
    }
}
"""

SYMBOL_MATRIX_SOURCE = """\
package demo;

final class SymbolMatrix {
    static String fixed(String value, String unused) {
        return value;
    }

    static String spread(String value, Object... unused) {
        return value;
    }

    static String recursive(String value, String unused) {
        return value;
    }

    static String overloaded(String value, String unused) {
        return value;
    }

    static String overloaded(String value, int unused) {
        return value;
    }

    private String privateFixed(String value, String unused) {
        return value;
    }

    String virtualFixed(String value, String unused) {
        return value;
    }

    static native String nativeFixed(String value, String unused);

    static String callPrivate() {
        return new SymbolMatrix("private", 0).privateFixed("kept", "noise");
    }

    SymbolMatrix(String value, int unused) {}
}
"""

SYMBOL_MATRIX_CALLER = """\
package demo;

final class SymbolMatrixCaller {
    static void call() {
        SymbolMatrix.fixed("a", "噪声甲");
        SymbolMatrix.fixed("b", "noise-two");
        SymbolMatrix.spread("empty");
        SymbolMatrix.spread("many", 1, 2, 3);
        SymbolMatrix.recursive(
                "outer", SymbolMatrix.recursive("inner", "nested-noise"));
        SymbolMatrix.overloaded("text", "noise");
        SymbolMatrix.overloaded("number", 7);
        SymbolMatrix.nativeFixed("native", "noise");
        new SymbolMatrix("virtual", 3).virtualFixed("kept", "noise");
        new SymbolMatrix("first", 1);
        new SymbolMatrix("second", 2);
    }
}
"""


def _create_java_classpath_fixture(root: Path) -> tuple:
    dependency_name = (
        "\u4f9d\u8d56 space:semicolon"
        if os.name == "nt"
        else "\u4f9d\u8d56 space:colon"
    )
    dependency = root / dependency_name
    external_source = dependency / "src" / "external" / "ExternalValue.java"
    external_source.parent.mkdir(parents=True)
    external_source.write_text(
        """\
package external;

public final class ExternalValue {
    public static String binary(String kept, String unused) {
        return kept;
    }
}
""",
        encoding="utf-8",
    )
    external_classes = dependency / "classes"
    external_classes.mkdir()
    completed = subprocess.run(
        [
            "javac",
            "-encoding",
            "UTF-8",
            "-d",
            str(external_classes),
            str(external_source),
        ],
        text=True,
        capture_output=True,
        timeout=20,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)

    jar_path = dependency / "external api.jar"
    with zipfile.ZipFile(jar_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for class_file in sorted(external_classes.rglob("*.class")):
            archive.write(
                class_file,
                class_file.relative_to(external_classes).as_posix(),
            )

    source = root / "subject"
    source.mkdir()
    java_file = source / "ClasspathOverloads.java"
    java_file.write_text(
        """\
import external.ExternalValue;

final class ClasspathOverloads {
    static String choose(String value, String unused) {
        return value;
    }

    static String choose(ExternalValue value, String unused) {
        return value.toString();
    }

    static void call() {
        choose("text", "string-noise");
        choose(new ExternalValue(), "external-noise");
        ExternalValue.binary("binary-kept", "binary-noise");
    }
}
""",
        encoding="utf-8",
    )
    return source, java_file, jar_path, external_classes


class JavaCandidateCombinationTest(unittest.TestCase):
    def test_overlapping_java_candidates_cannot_be_combined(self) -> None:
        content = b"abcdef"
        first = JavaTarget(
            path=Path("Example.java"),
            kind="member",
            start=1,
            end=4,
            label="first",
            content_hash=hashlib.sha256(content[1:4]).hexdigest(),
        )
        second = JavaTarget(
            path=Path("Example.java"),
            kind="expression",
            start=3,
            end=5,
            label="second",
            content_hash=hashlib.sha256(content[3:5]).hexdigest(),
        )

        self.assertIsNone(_combine_java_candidates([first, second]))


@unittest.skipUnless(
    shutil.which("java") and shutil.which("javac"),
    "a JDK is required for the native Java reducer test",
)
class JavaReducerTest(unittest.TestCase):
    def test_rechecks_epoch_rejections_after_other_java_reductions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            java_file = source / "Epoch.java"
            source.mkdir()
            java_file.write_text(
                "final class Epoch {\n"
                "    static void first() {}\n"
                "    static void second() {}\n"
                "    static void unrelated() {}\n"
                "}\n",
                encoding="utf-8",
            )
            (source / "oracle.py").write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "text = Path('Epoch.java').read_text(encoding='utf-8')\n"
                "if 'first()' not in text and 'second()' in text:\n"
                "    print('DIFFERENT_FAILURE')\n"
                "    raise SystemExit(2)\n"
                "print('ORIGINAL_FAILURE')\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            session = ReductionSession(
                source,
                FailureOracle(
                    CommandRunner("python3 oracle.py", timeout_seconds=5),
                    FailureSpec("ORIGINAL_FAILURE"),
                ),
                ReductionStats(source_files=2, source_bytes=0),
                jobs=1,
            )
            try:
                session.verify_baseline(1)
                self.assertTrue(JavaReducer(session).reduce())
                reduced = (session.current / "Epoch.java").read_text(encoding="utf-8")

                self.assertNotIn("first()", reduced)
                self.assertNotIn("second()", reduced)
                self.assertTrue(session.oracle.accepts(session.run_current()))
            finally:
                session.close()

    def test_reduces_java_ast_without_losing_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            java_file = source / "src" / "demo" / "Trigger.java"
            java_file.parent.mkdir(parents=True)
            java_file.write_text(JAVA_SOURCE, encoding="utf-8")

            command = (
                "mkdir -p target/classes "
                "&& javac -d target/classes src/demo/Trigger.java "
                "&& java -cp target/classes demo.Trigger"
            )
            runner = CommandRunner(command, timeout_seconds=20)
            oracle = FailureOracle(runner, FailureSpec("NoSuchMethodError"))
            stats = ReductionStats(source_files=1, source_bytes=len(JAVA_SOURCE))
            session = ReductionSession(source, oracle, stats)
            try:
                oracle.verify_baseline(
                    session.current,
                    repeat=2,
                    reset=session.clean_generated,
                )
                changed = JavaReducer(session).reduce()
                reduced = (session.current / "src" / "demo" / "Trigger.java").read_text(
                    encoding="utf-8"
                )

                self.assertTrue(changed)
                self.assertNotIn("java.util.List", reduced)
                self.assertNotIn("UNUSED_FIELD", reduced)
                self.assertNotIn("unusedMethod", reduced)
                self.assertNotIn("noise", reduced)
                self.assertIn("main", reduced)
                self.assertIn("NoSuchMethodError", reduced)
                self.assertFalse((session.current / "target").exists())
                self.assertTrue(oracle.accepts(session.run_current()))
            finally:
                session.close()

    def test_reduces_annotations_parameters_arguments_and_expressions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            java_file = source / "src" / "demo" / "DeepTrigger.java"
            java_file.parent.mkdir(parents=True)
            java_file.write_text(DEEP_JAVA_SOURCE, encoding="utf-8")

            command = (
                "mkdir -p target/classes "
                "&& javac -d target/classes src/demo/DeepTrigger.java "
                "&& java -cp target/classes demo.DeepTrigger"
            )
            runner = CommandRunner(command, timeout_seconds=20)
            oracle = FailureOracle(runner, FailureSpec("DEEP_ORIGINAL_FAILURE"))
            session = ReductionSession(
                source,
                oracle,
                ReductionStats(source_files=1, source_bytes=len(DEEP_JAVA_SOURCE)),
            )
            try:
                session.verify_baseline(2)
                self.assertTrue(JavaReducer(session).reduce())
                reduced = java_file.with_name("DeepTrigger.java")
                reduced = (
                    session.current / reduced.relative_to(source)
                ).read_text(encoding="utf-8")

                descriptions = [event.description for event in session.stats.events]
                self.assertTrue(any("Java annotation" in item for item in descriptions))
                self.assertTrue(any("Java parameter" in item for item in descriptions))
                self.assertTrue(any("Java argument" in item for item in descriptions))
                self.assertTrue(any("Java expression" in item for item in descriptions))
                self.assertNotIn("@SuppressWarnings", reduced)
                self.assertNotIn("@Deprecated", reduced)
                self.assertNotIn("Object unused", reduced)
                self.assertNotIn(" ? ", reduced)
                self.assertIn("DEEP_ORIGINAL_FAILURE", reduced)
                self.assertTrue(oracle.accepts(session.run_current()))
            finally:
                session.close()

    def test_symbol_aware_parameter_removal_updates_cross_file_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            package = source / "src" / "demo"
            package.mkdir(parents=True)
            (package / "Parts.java").write_text(
                COORDINATED_HELPER_SOURCE, encoding="utf-8"
            )
            (package / "CoordinatedTrigger.java").write_text(
                COORDINATED_TRIGGER_SOURCE, encoding="utf-8"
            )

            command = (
                "mkdir -p target/classes "
                "&& javac -encoding UTF-8 -d target/classes src/demo/*.java "
                "&& java -cp target/classes demo.CoordinatedTrigger"
            )
            oracle = FailureOracle(
                CommandRunner(command, timeout_seconds=20),
                FailureSpec("COORDINATED_FAILURE"),
            )
            session = ReductionSession(
                source,
                oracle,
                ReductionStats(source_files=2, source_bytes=0),
            )
            try:
                session.verify_baseline(2)
                self.assertTrue(JavaReducer(session).reduce())
                helper = (session.current / "src" / "demo" / "Parts.java").read_text(
                    encoding="utf-8"
                )
                trigger = (
                    session.current / "src" / "demo" / "CoordinatedTrigger.java"
                ).read_text(encoding="utf-8")

                self.assertIn("keep(String value)", helper)
                self.assertNotIn("String unused", helper)
                self.assertIn('Parts.keep("COORDINATED_")', trigger)
                self.assertIn('Parts.keep("FAILURE")', trigger)
                self.assertTrue(
                    any(
                        "coordinated call-site edit(s)" in event.description
                        for event in session.stats.events
                    )
                )
                self.assertTrue(oracle.accepts(session.run_current()))
            finally:
                session.close()

    def test_symbol_groups_are_atomic_and_cover_varargs_and_constructors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper_path = root / "SymbolMatrix.java"
            caller_path = root / "SymbolMatrixCaller.java"
            helper_path.write_text(SYMBOL_MATRIX_SOURCE, encoding="utf-8")
            caller_path.write_text(SYMBOL_MATRIX_CALLER, encoding="utf-8")

            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)
                groups = [
                    target for target in targets if isinstance(target, JavaChangeSet)
                ]
                fixed = next(
                    target
                    for target in groups
                    if "#fixed(" in target.label and target.label.endswith(":1")
                )
                spread = next(
                    target
                    for target in groups
                    if "#spread(" in target.label and target.label.endswith(":1")
                )
                constructor = next(
                    target
                    for target in groups
                    if "#SymbolMatrix(" in target.label
                    and target.label.endswith(":1")
                )
                recursive = next(
                    target
                    for target in groups
                    if "#recursive(" in target.label and target.label.endswith(":1")
                )
                private_fixed = next(
                    target
                    for target in groups
                    if "#privateFixed(" in target.label
                    and target.label.endswith(":1")
                )
                virtual_fixed = next(
                    target
                    for target in groups
                    if "#virtualFixed(" in target.label
                    and target.label.endswith(":1")
                )
                overloads = [
                    target
                    for target in groups
                    if "#overloaded(" in target.label
                    and target.label.endswith(":1")
                ]

                self.assertEqual(3, len(fixed.targets))
                self.assertEqual(2, len(spread.targets))
                self.assertEqual(5, len(constructor.targets))
                self.assertEqual(2, len(recursive.targets))
                self.assertEqual(2, len(private_fixed.targets))
                self.assertEqual(2, len(virtual_fixed.targets))
                self.assertEqual(2, len(overloads))
                self.assertEqual(2, len({target.label for target in overloads}))
                self.assertTrue(all(len(target.targets) == 2 for target in overloads))
                self.assertFalse(any("#nativeFixed(" in target.label for target in groups))
                fixed_call_text = [
                    (root / edit.path).read_bytes()[edit.start : edit.end].decode(
                        "utf-8"
                    )
                    for edit in fixed.targets
                    if edit.path.name == "SymbolMatrixCaller.java"
                ]
                self.assertEqual([', "噪声甲"', ', "noise-two"'], fixed_call_text)
                spread_call = next(
                    edit
                    for edit in spread.targets
                    if edit.path.name == "SymbolMatrixCaller.java"
                )
                self.assertEqual(
                    ", 1, 2, 3",
                    caller_path.read_bytes()[
                        spread_call.start : spread_call.end
                    ].decode("utf-8"),
                )

                stale = next(
                    edit
                    for edit in fixed.targets
                    if edit.path.name == "SymbolMatrixCaller.java"
                )
                damaged = bytearray(caller_path.read_bytes())
                damaged[stale.start] = ord("!")
                caller_path.write_bytes(bytes(damaged))
                before = {
                    helper_path: helper_path.read_bytes(),
                    caller_path: caller_path.read_bytes(),
                }
                self.assertFalse(_apply_candidate(root, fixed))
                self.assertEqual(before[helper_path], helper_path.read_bytes())
                self.assertEqual(before[caller_path], caller_path.read_bytes())

                caller_path.write_text(SYMBOL_MATRIX_CALLER, encoding="utf-8")
                self.assertTrue(_apply_candidate(root, fixed))
                self.assertIn(
                    "fixed(String value)", helper_path.read_text(encoding="utf-8")
                )

                updated = analyzer.analyze(root)
                string_overload = next(
                    target
                    for target in updated
                    if isinstance(target, JavaChangeSet)
                    and "#overloaded(java.lang.String,java.lang.String):1"
                    in target.label
                )
                self.assertTrue(_apply_candidate(root, string_overload))
                helper = helper_path.read_text(encoding="utf-8")
                caller = caller_path.read_text(encoding="utf-8")
                self.assertIn("overloaded(String value)", helper)
                self.assertIn("overloaded(String value, int unused)", helper)
                self.assertIn('overloaded("text")', caller)
                self.assertIn('overloaded("number", 7)', caller)

                updated = analyzer.analyze(root)
                updated_spread = next(
                    target
                    for target in updated
                    if isinstance(target, JavaChangeSet)
                    and "#spread(" in target.label
                    and target.label.endswith(":1")
                )
                self.assertTrue(_apply_candidate(root, updated_spread))
                updated = analyzer.analyze(root)
                updated_recursive = next(
                    target
                    for target in updated
                    if isinstance(target, JavaChangeSet)
                    and "#recursive(" in target.label
                    and target.label.endswith(":1")
                )
                self.assertTrue(_apply_candidate(root, updated_recursive))
                recursive_caller = "".join(
                    caller_path.read_text(encoding="utf-8").split()
                )
                self.assertIn('SymbolMatrix.recursive("outer");', recursive_caller)
                self.assertNotIn("nested-noise", recursive_caller)
                self.assertNotIn('recursive("inner"', recursive_caller)

                updated = analyzer.analyze(root)
                updated_constructor = next(
                    target
                    for target in updated
                    if isinstance(target, JavaChangeSet)
                    and "#SymbolMatrix(" in target.label
                    and target.label.endswith(":1")
                )
                self.assertTrue(_apply_candidate(root, updated_constructor))

                updated = analyzer.analyze(root)
                updated_private_fixed = next(
                    target
                    for target in updated
                    if isinstance(target, JavaChangeSet)
                    and "#privateFixed(" in target.label
                    and target.label.endswith(":1")
                )
                self.assertTrue(_apply_candidate(root, updated_private_fixed))

                updated = analyzer.analyze(root)
                updated_virtual_fixed = next(
                    target
                    for target in updated
                    if isinstance(target, JavaChangeSet)
                    and "#virtualFixed(" in target.label
                    and target.label.endswith(":1")
                )
                self.assertTrue(_apply_candidate(root, updated_virtual_fixed))

            helper = helper_path.read_text(encoding="utf-8")
            caller = caller_path.read_text(encoding="utf-8")
            self.assertIn("spread(String value)", helper)
            self.assertIn("SymbolMatrix(String value)", helper)
            self.assertIn('spread("empty")', caller)
            self.assertIn('spread("many")', caller)
            self.assertIn('new SymbolMatrix("first")', caller)
            self.assertIn('new SymbolMatrix("second")', caller)
            self.assertIn("privateFixed(String value)", helper)
            self.assertIn('privateFixed("kept")', helper)
            self.assertNotIn('privateFixed("kept", "noise")', helper)
            self.assertIn("virtualFixed(String value)", helper)
            self.assertIn('virtualFixed("kept")', caller)
            self.assertNotIn('virtualFixed("kept", "noise")', caller)
            self.assertIn('nativeFixed("native", "noise")', caller)

            completed = subprocess.run(
                [
                    "javac",
                    "-encoding",
                    "UTF-8",
                    "-d",
                    str(root / "classes"),
                    str(helper_path),
                    str(caller_path),
                ],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

    def test_final_instance_and_final_class_methods_link_calls(self) -> None:
        definitions_source = """\
class FinalMethodHost {
    final String fixed(String value, String unused) {
        return value;
    }

    final String overloaded(String value, String unused) {
        return value;
    }

    final String overloaded(String value, int unused) {
        return value;
    }

    final String spread(String value, Object... unused) {
        return value;
    }

    void direct() {
        fixed("same", "same-noise");
    }
}

final class FinalClassHost {
    String cross(String value, String unused) {
        return value;
    }
}
"""
        caller_source = """\
final class InstanceCaller {
    static void call() {
        FinalMethodHost host = new FinalMethodHost();
        host.fixed("cross", "cross-noise");
        host.overloaded("text", "overload-noise");
        host.overloaded("number", 7);
        host.spread("many", 1, 2, 3);
        new FinalClassHost().cross("kept", "cross-class-noise");
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            definitions = root / "InstanceDefinitions.java"
            caller = root / "InstanceCaller.java"
            definitions.write_text(definitions_source, encoding="utf-8")
            caller.write_text(caller_source, encoding="utf-8")

            labels = {
                "fixed": "FinalMethodHost#fixed(java.lang.String,java.lang.String):1",
                "string_overload": (
                    "FinalMethodHost#overloaded"
                    "(java.lang.String,java.lang.String):1"
                ),
                "int_overload": (
                    "FinalMethodHost#overloaded(java.lang.String,int):1"
                ),
                "spread": (
                    "FinalMethodHost#spread"
                    "(java.lang.String,java.lang.Object...):1"
                ),
                "cross": (
                    "FinalClassHost#cross(java.lang.String,java.lang.String):1"
                ),
            }
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)
                groups = {
                    target.label: target
                    for target in targets
                    if isinstance(target, JavaChangeSet)
                }
                self.assertEqual(set(labels.values()), set(groups))
                self.assertEqual(3, len(groups[labels["fixed"]].targets))
                for name in ("string_overload", "int_overload", "spread", "cross"):
                    self.assertEqual(2, len(groups[labels[name]].targets))

                source_by_path = {
                    path.relative_to(root): path.read_bytes()
                    for path in (definitions, caller)
                }

                def selected(group: JavaChangeSet) -> set:
                    return {
                        source_by_path[target.path][target.start : target.end].decode(
                            "utf-8"
                        )
                        for target in group.targets
                    }

                self.assertEqual(
                    {
                        ", String unused",
                        ', "same-noise"',
                        ', "cross-noise"',
                    },
                    selected(groups[labels["fixed"]]),
                )
                self.assertIn(
                    ', "overload-noise"',
                    selected(groups[labels["string_overload"]]),
                )
                self.assertIn(
                    ", 7",
                    selected(groups[labels["int_overload"]]),
                )
                self.assertEqual(
                    {", Object... unused", ", 1, 2, 3"},
                    selected(groups[labels["spread"]]),
                )
                self.assertIn(
                    ', "cross-class-noise"',
                    selected(groups[labels["cross"]]),
                )

                for name in ("fixed", "string_overload", "spread", "cross"):
                    updated = analyzer.analyze(root)
                    group = next(
                        target
                        for target in updated
                        if isinstance(target, JavaChangeSet)
                        and target.label == labels[name]
                    )
                    self.assertTrue(_apply_candidate(root, group))

            completed = subprocess.run(
                [
                    "javac",
                    "-encoding",
                    "UTF-8",
                    "-d",
                    str(root / "classes"),
                    str(definitions),
                    str(caller),
                ],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            reduced_definitions = definitions.read_text(encoding="utf-8")
            reduced_caller = caller.read_text(encoding="utf-8")
            self.assertIn("fixed(String value)", reduced_definitions)
            self.assertIn("spread(String value)", reduced_definitions)
            self.assertIn("cross(String value)", reduced_definitions)
            self.assertIn('host.fixed("cross")', reduced_caller)
            self.assertIn('host.overloaded("text")', reduced_caller)
            self.assertIn('host.overloaded("number", 7)', reduced_caller)
            self.assertIn('host.spread("many")', reduced_caller)

    def test_instance_parameter_and_method_references_remain_blockers(self) -> None:
        source_text = """\
import java.util.function.BiFunction;

final class ReferenceHost {
    String body(String value, String unused) {
        return value;
    }

    String referenced(String value, String unused) {
        return value;
    }

    void calls() {
        body("kept", "body-noise");
        referenced("kept", "reference-noise");
        BiFunction<String, String, String> function = this::referenced;
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            java_file = root / "ReferenceHost.java"
            java_file.write_text(source_text, encoding="utf-8")
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            groups = [
                target for target in targets if isinstance(target, JavaChangeSet)
            ]
            expected_label = (
                "ReferenceHost#body(java.lang.String,java.lang.String):1"
            )
            self.assertEqual([expected_label], [target.label for target in groups])
            self.assertEqual(2, len(groups[0].targets))
            self.assertFalse(
                any(
                    "#body(" in target.label and target.label.endswith(":0")
                    for target in groups
                )
            )
            self.assertFalse(any("#referenced(" in target.label for target in groups))
            self.assertTrue(_apply_candidate(root, groups[0]))
            reduced = java_file.read_text(encoding="utf-8")
            self.assertIn("body(String value)", reduced)
            self.assertIn('body("kept")', reduced)
            self.assertIn("this::referenced", reduced)
            completed = subprocess.run(
                ["javac", "-encoding", "UTF-8", str(java_file)],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

    def test_final_dispatch_excludes_overrides_and_open_virtual_methods(
        self,
    ) -> None:
        source_text = """\
class GenericBase<T> {
    T inherited(T value, String unused) {
        return value;
    }

    String emerging(String value) {
        return value;
    }

    T generic(T value) {
        return value;
    }
}

class FinalOverride extends GenericBase<String> {
    @Override
    public final String inherited(String value, String unused) {
        return value;
    }

    final String emerging(String value, String unused) {
        return value;
    }

    final String generic(String value, String unused) {
        return value;
    }

    final String ownFinal(String value, String unused) {
        return value;
    }
}

final class ClosedOverride extends GenericBase<String> {
    @Override
    public String inherited(String value, String unused) {
        return value;
    }

    String ownClosed(String value, String unused) {
        return value;
    }
}

interface Contract {
    String api(String value, String unused);
}

final class ContractImpl implements Contract {
    @Override
    public String api(String value, String unused) {
        return value;
    }

    String ownImplementation(String value, String unused) {
        return value;
    }
}

class OpenHost {
    String virtual(String value, String unused) {
        return value;
    }
}

final class DispatchCalls {
    static void call() {
        FinalOverride child = new FinalOverride();
        child.inherited("kept", "override-noise");
        child.emerging("kept", "emerging-noise");
        child.generic("kept", "generic-noise");
        child.ownFinal("kept", "own-final-noise");

        ClosedOverride closed = new ClosedOverride();
        closed.inherited("kept", "closed-override-noise");
        closed.ownClosed("kept", "own-closed-noise");

        Contract contract = new ContractImpl();
        contract.api("kept", "contract-noise");
        new ContractImpl().api("kept", "direct-contract-noise");
        new ContractImpl().ownImplementation("kept", "implementation-noise");
        new OpenHost().virtual("kept", "virtual-noise");
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            java_file = root / "DispatchMatrix.java"
            java_file.write_text(source_text, encoding="utf-8")
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            groups = [
                target for target in targets if isinstance(target, JavaChangeSet)
            ]
            expected = {
                "FinalOverride#ownFinal(java.lang.String,java.lang.String):1",
                "ClosedOverride#ownClosed(java.lang.String,java.lang.String):1",
                (
                    "ContractImpl#ownImplementation"
                    "(java.lang.String,java.lang.String):1"
                ),
            }
            self.assertEqual(expected, {target.label for target in groups})
            self.assertTrue(all(len(target.targets) == 2 for target in groups))
            for blocked_name in (
                "#inherited(",
                "#emerging(",
                "#generic(",
                "#api(",
                "#virtual(",
            ):
                self.assertFalse(
                    any(blocked_name in target.label for target in groups),
                    blocked_name,
                )

    def test_special_owner_kinds_do_not_gain_instance_groups(self) -> None:
        source_text = """\
final class TopLevelHost {
    String safe(String value, String unused) {
        return value;
    }

    static void call() {
        new TopLevelHost().safe("kept", "top-level-noise");
    }
}

enum EnumHost {
    ONE;

    final String enumOnly(String value, String unused) {
        return value;
    }

    static void call() {
        ONE.enumOnly("kept", "enum-noise");
    }
}

class OwnerBoundaries {
    static final class MemberHost {
        String memberOnly(String value, String unused) {
            return value;
        }
    }

    static void callMember() {
        new MemberHost().memberOnly("kept", "member-noise");
    }

    static final Object ANONYMOUS = new Object() {
        final String anonymousOnly(String value, String unused) {
            return value;
        }

        void callAnonymous() {
            anonymousOnly("kept", "anonymous-noise");
        }
    };

    static void callLocal() {
        final class LocalHost {
            String localOnly(String value, String unused) {
                return value;
            }
        }
        new LocalHost().localOnly("kept", "local-noise");
    }
}
"""
        record_source = """\
record RecordHost() {
    final String recordOnly(String value, String unused) {
        return value;
    }

    static void call() {
        new RecordHost().recordOnly("kept", "record-noise");
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "OwnerBoundaries.java").write_text(
                source_text,
                encoding="utf-8",
            )
            version = subprocess.run(
                ["javac", "-version"],
                text=True,
                capture_output=True,
                timeout=10,
            )
            version_text = (version.stdout or version.stderr).strip().split()[-1]
            if int(version_text.split(".")[0]) >= 16:
                (root / "RecordHost.java").write_text(
                    record_source,
                    encoding="utf-8",
                )

            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            groups = [
                target for target in targets if isinstance(target, JavaChangeSet)
            ]
            self.assertEqual(2, len(groups))
            self.assertTrue(
                any("#safe(" in target.label for target in groups)
            )
            self.assertTrue(
                any("#memberOnly(" in target.label for target in groups)
            )
            self.assertTrue(all(len(target.targets) == 2 for target in groups))
            for blocked_name in (
                "#enumOnly(",
                "#anonymousOnly(",
                "#localOnly(",
                "#recordOnly(",
            ):
                self.assertFalse(
                    any(blocked_name in target.label for target in groups),
                    blocked_name,
                )

    def test_missing_external_hierarchy_disables_instance_groups_globally(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external_source = root / "external-src" / "ext"
            external_source.mkdir(parents=True)
            base_source = external_source / "ExternalBase.java"
            marker_source = external_source / "ExternalMarker.java"
            base_source.write_text(
                """\
package ext;

public class ExternalBase {}
""",
                encoding="utf-8",
            )
            marker_source.write_text(
                """\
package ext;

public interface ExternalMarker {
    String externalApi(String value, String unused);
}
""",
                encoding="utf-8",
            )
            external_classes = root / "external-classes"
            external_classes.mkdir()
            completed = subprocess.run(
                [
                    "javac",
                    "-encoding",
                    "UTF-8",
                    "-d",
                    str(external_classes),
                    str(base_source),
                    str(marker_source),
                ],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

            cases = [
                (
                    "superclass",
                    "import ext.ExternalBase;",
                    "extends ExternalBase",
                    "",
                    "",
                ),
                (
                    "interface",
                    "import ext.ExternalMarker;",
                    "implements ExternalMarker",
                    """\
    @Override
    public String externalApi(String value, String unused) {
        return value;
    }
""",
                    (
                        "new HierarchyCase().externalApi"
                        "(\"kept\", \"external-noise\");"
                    ),
                ),
            ]
            with _JavaStructureAnalyzer() as without_classpath_analyzer:
                for name, import_text, hierarchy, extra_method, extra_call in cases:
                    with self.subTest(hierarchy=name):
                        source = root / name
                        source.mkdir()
                        java_file = source / "HierarchyCase.java"
                        java_file.write_text(
                            """\
%s

final class HierarchyCase %s {
    String safe(String value, String unused) {
        return value;
    }

%s
}

class IndependentHost {
    final String independent(String value, String unused) {
        return value;
    }
}

final class HierarchyCalls {
    static void call() {
        new HierarchyCase().safe("kept", "hierarchy-noise");
        new IndependentHost().independent("kept", "independent-noise");
        %s
    }
}
"""
                            % (
                                import_text,
                                hierarchy,
                                extra_method,
                                extra_call,
                            ),
                            encoding="utf-8",
                        )

                        without_classpath = without_classpath_analyzer.analyze(source)
                        self.assertFalse(
                            any(
                                isinstance(target, JavaChangeSet)
                                for target in without_classpath
                            )
                        )
                        syntax_kinds = {
                            target.kind
                            for target in without_classpath
                            if isinstance(target, JavaTarget)
                        }
                        self.assertTrue({"parameter", "argument"} <= syntax_kinds)

                        classpath = prepare_java_analysis_classpath(
                            source,
                            [str(external_classes)],
                        )
                        with _JavaStructureAnalyzer(classpath) as analyzer:
                            with_classpath = analyzer.analyze(source)
                        groups = [
                            target
                            for target in with_classpath
                            if isinstance(target, JavaChangeSet)
                        ]
                        self.assertEqual(
                            {
                                (
                                    "HierarchyCase#safe"
                                    "(java.lang.String,java.lang.String):1"
                                ),
                                (
                                    "IndependentHost#independent"
                                    "(java.lang.String,java.lang.String):1"
                                ),
                            },
                            {target.label for target in groups},
                        )
                        self.assertTrue(
                            all(len(target.targets) == 2 for target in groups)
                        )
                        self.assertFalse(
                            any("#externalApi(" in target.label for target in groups)
                        )

    def test_explicit_classpath_resolves_external_overloads_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, java_file, jar_path, _external_classes = (
                _create_java_classpath_fixture(Path(directory))
            )

            with _JavaStructureAnalyzer() as analyzer:
                without_classpath = analyzer.analyze(source)
            self.assertFalse(
                any(
                    isinstance(target, JavaChangeSet)
                    for target in without_classpath
                )
            )
            self.assertTrue(
                {
                    "argument",
                    "import",
                    "literal",
                    "member",
                    "parameter",
                    "statement",
                }.issubset(
                    {
                        target.kind
                        for target in without_classpath
                        if isinstance(target, JavaTarget)
                    }
                )
            )

            classpath = prepare_java_analysis_classpath(source, [str(jar_path)])
            self.assertEqual(1, len(classpath))
            self.assertEqual(jar_path.resolve(), classpath[0].path)
            self.assertEqual("file", classpath[0].kind)
            self.assertIn("\u4f9d\u8d56", str(classpath[0].path))
            self.assertIn(" ", str(classpath[0].path))
            self.assertIn(";" if os.name == "nt" else ":", str(classpath[0].path))

            expected_calls = {
                "ClasspathOverloads#choose(java.lang.String,java.lang.String):1": (
                    ', "string-noise"'
                ),
                "ClasspathOverloads#choose(external.ExternalValue,java.lang.String):1": (
                    ', "external-noise"'
                ),
            }
            with _JavaStructureAnalyzer(classpath) as analyzer:
                targets = analyzer.analyze(source)
                groups = [
                    target
                    for target in targets
                    if isinstance(target, JavaChangeSet)
                ]
                choose_groups = {
                    target.label: target
                    for target in groups
                    if "#choose(" in target.label
                    and target.label.endswith(":1")
                }
                self.assertEqual(set(expected_calls), set(choose_groups))
                self.assertFalse(any("#binary(" in target.label for target in groups))
                source_bytes = java_file.read_bytes()
                for label, call_text in expected_calls.items():
                    group = choose_groups[label]
                    self.assertEqual(2, len(group.targets))
                    selected = [
                        source_bytes[target.start : target.end].decode("utf-8")
                        for target in group.targets
                    ]
                    self.assertIn(", String unused", selected)
                    self.assertIn(call_text, selected)

                for label in expected_calls:
                    updated = analyzer.analyze(source)
                    group = next(
                        target
                        for target in updated
                        if isinstance(target, JavaChangeSet)
                        and target.label == label
                    )
                    self.assertTrue(_apply_candidate(source, group))

            reduced = java_file.read_text(encoding="utf-8")
            self.assertIn("choose(String value)", reduced)
            self.assertIn("choose(ExternalValue value)", reduced)
            self.assertIn('choose("text");', reduced)
            self.assertIn("choose(new ExternalValue());", reduced)
            self.assertIn(
                'ExternalValue.binary("binary-kept", "binary-noise");',
                reduced,
            )

            compile_jar = Path(directory) / "external-api.jar"
            shutil.copy2(jar_path, compile_jar)
            completed = subprocess.run(
                [
                    "javac",
                    "-encoding",
                    "UTF-8",
                    "-cp",
                    str(compile_jar),
                    "-d",
                    str(source / "classes"),
                    str(java_file),
                ],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

    def test_classpath_accepts_relative_classes_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, _java_file, _jar_path, external_classes = (
                _create_java_classpath_fixture(Path(directory))
            )
            relative = os.path.relpath(external_classes, source)

            classpath = prepare_java_analysis_classpath(source, [relative])

            self.assertEqual(1, len(classpath))
            self.assertEqual(external_classes.resolve(), classpath[0].path)
            self.assertEqual("directory", classpath[0].kind)
            with _JavaStructureAnalyzer(classpath) as analyzer:
                targets = analyzer.analyze(source)
            groups = [
                target for target in targets if isinstance(target, JavaChangeSet)
            ]
            self.assertEqual(
                {
                    "ClasspathOverloads#choose(java.lang.String,java.lang.String):1",
                    "ClasspathOverloads#choose(external.ExternalValue,java.lang.String):1",
                },
                {target.label for target in groups},
            )
            self.assertTrue(all(len(target.targets) == 2 for target in groups))

    def test_classpath_entry_order_controls_binary_symbol_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, java_file, _jar_path, string_classes = (
                _create_java_classpath_fixture(root)
            )
            variant_source = root / "variant-src" / "external" / "ExternalValue.java"
            variant_source.parent.mkdir(parents=True)
            variant_source.write_text(
                """\
package external;

public final class ExternalValue {
    public static ExternalValue binary(String kept, String unused) {
        return new ExternalValue();
    }
}
""",
                encoding="utf-8",
            )
            value_classes = root / "value-classes"
            value_classes.mkdir()
            completed = subprocess.run(
                [
                    "javac",
                    "-encoding",
                    "UTF-8",
                    "-d",
                    str(value_classes),
                    str(variant_source),
                ],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            java_file.write_text(
                """\
import external.ExternalValue;

final class ClasspathOrder {
    static String choose(String value, String unused) {
        return value;
    }

    static String choose(ExternalValue value, String unused) {
        return value.toString();
    }

    static void call() {
        choose(ExternalValue.binary("kept", "binary-noise"), "call-noise");
    }
}
""",
                encoding="utf-8",
            )

            cases = [
                (
                    (string_classes, value_classes),
                    "ClasspathOrder#choose(java.lang.String,java.lang.String):1",
                ),
                (
                    (value_classes, string_classes),
                    "ClasspathOrder#choose(external.ExternalValue,java.lang.String):1",
                ),
            ]
            for paths, expected_label in cases:
                with self.subTest(first=paths[0].name):
                    classpath = prepare_java_analysis_classpath(
                        source,
                        [str(path) for path in paths],
                    )
                    self.assertEqual(
                        [path.resolve() for path in paths],
                        [entry.path for entry in classpath],
                    )
                    with _JavaStructureAnalyzer(classpath) as analyzer:
                        targets = analyzer.analyze(source)
                    groups = [
                        target
                        for target in targets
                        if isinstance(target, JavaChangeSet)
                    ]
                    self.assertEqual(
                        [expected_label],
                        [target.label for target in groups],
                    )
                    self.assertEqual(2, len(groups[0].targets))
                    source_bytes = java_file.read_bytes()
                    selected = [
                        source_bytes[target.start : target.end].decode("utf-8")
                        for target in groups[0].targets
                    ]
                    self.assertIn(", String unused", selected)
                    self.assertIn(', "call-noise"', selected)

    def test_classpath_change_or_deletion_is_rejected_between_analyses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, _java_file, jar_path, _external_classes = (
                _create_java_classpath_fixture(Path(directory))
            )
            original = jar_path.read_bytes()
            classpath = prepare_java_analysis_classpath(source, [str(jar_path)])

            with _JavaStructureAnalyzer(classpath) as analyzer:
                self.assertTrue(analyzer.analyze(source))
                jar_path.write_bytes(original + b"changed")
                with self.assertRaisesRegex(
                    JavaReducerError,
                    "changed after validation",
                ):
                    analyzer.analyze(source)

                jar_path.write_bytes(original)
                self.assertTrue(analyzer.analyze(source))
                jar_path.unlink()
                with self.assertRaisesRegex(
                    JavaReducerError,
                    "missing or unreadable",
                ):
                    analyzer.analyze(source)

                jar_path.mkdir()
                with self.assertRaisesRegex(
                    JavaReducerError,
                    "changed after validation",
                ):
                    analyzer.analyze(source)

    def test_classpath_rejects_entry_replaced_by_existing_hardlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _java_file, jar_path, _external_classes = (
                _create_java_classpath_fixture(root)
            )
            second_jar = root / "second-external.jar"
            shutil.copy2(jar_path, second_jar)
            self.assertEqual(jar_path.read_bytes(), second_jar.read_bytes())
            self.assertFalse(jar_path.samefile(second_jar))
            classpath = prepare_java_analysis_classpath(
                source,
                [str(jar_path), str(second_jar)],
            )
            self.assertEqual(2, len(classpath))

            with _JavaStructureAnalyzer(classpath) as analyzer:
                self.assertTrue(analyzer.analyze(source))
                second_jar.unlink()
                os.link(jar_path, second_jar)
                self.assertTrue(jar_path.samefile(second_jar))
                with self.assertRaisesRegex(JavaReducerError, "duplicate"):
                    analyzer.analyze(source)

    def test_classpath_directory_tree_changes_are_rejected_between_analyses(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, _java_file, _jar_path, external_classes = (
                _create_java_classpath_fixture(Path(directory))
            )
            class_file = next(external_classes.rglob("*.class"))
            original = class_file.read_bytes()
            classpath = prepare_java_analysis_classpath(
                source,
                [str(external_classes)],
            )

            with _JavaStructureAnalyzer(classpath) as analyzer:
                self.assertTrue(analyzer.analyze(source))

                if os.name == "posix":
                    original_mode = stat.S_IMODE(external_classes.stat().st_mode)
                    external_classes.chmod(original_mode ^ stat.S_IWGRP)
                    try:
                        with self.assertRaisesRegex(
                            JavaReducerError,
                            "changed after validation",
                        ):
                            analyzer.analyze(source)
                    finally:
                        external_classes.chmod(original_mode)
                    self.assertTrue(analyzer.analyze(source))

                class_file.write_bytes(original + b"changed")
                with self.assertRaisesRegex(
                    JavaReducerError,
                    "changed after validation",
                ):
                    analyzer.analyze(source)
                class_file.write_bytes(original)
                self.assertTrue(analyzer.analyze(source))

                added_file = external_classes / "added-metadata.txt"
                added_file.write_text("added", encoding="utf-8")
                with self.assertRaisesRegex(
                    JavaReducerError,
                    "changed after validation",
                ):
                    analyzer.analyze(source)
                added_file.unlink()
                self.assertTrue(analyzer.analyze(source))

                class_file.unlink()
                with self.assertRaisesRegex(
                    JavaReducerError,
                    "changed after validation",
                ):
                    analyzer.analyze(source)

    def test_classpath_preparation_rejects_invalid_and_duplicate_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _java_file, jar_path, _external_classes = (
                _create_java_classpath_fixture(root)
            )

            invalid_entries = [
                ("", "must not be empty"),
                ("invalid\0classpath", "must not contain NUL"),
                ("missing-classpath.jar", "does not exist"),
            ]
            for value, message in invalid_entries:
                with self.subTest(value=value):
                    with self.assertRaisesRegex(JavaReducerError, message):
                        prepare_java_analysis_classpath(source, [value])

            relative = os.path.relpath(jar_path, source)
            with self.assertRaisesRegex(JavaReducerError, "duplicate"):
                prepare_java_analysis_classpath(
                    source,
                    [str(jar_path), relative],
                )

            hardlink = root / "classpath-hardlink.jar"
            os.link(jar_path, hardlink)
            with self.assertRaisesRegex(JavaReducerError, "duplicate"):
                prepare_java_analysis_classpath(
                    source,
                    [str(jar_path), str(hardlink)],
                )

            if os.name != "nt":
                top_level_link = root / "classpath-link.jar"
                top_level_link.symlink_to(jar_path)
                linked_classpath = prepare_java_analysis_classpath(
                    source,
                    [str(top_level_link)],
                )
                self.assertEqual(jar_path.resolve(), linked_classpath[0].path)
                with self.assertRaisesRegex(JavaReducerError, "duplicate"):
                    prepare_java_analysis_classpath(
                        source,
                        [str(jar_path), str(top_level_link)],
                    )

                directory_with_link = root / "directory-with-link"
                directory_with_link.mkdir()
                (directory_with_link / "linked.class").symlink_to(jar_path)
                with self.assertRaisesRegex(JavaReducerError, "symbolic links"):
                    prepare_java_analysis_classpath(
                        source,
                        [str(directory_with_link)],
                    )

                directory_with_fifo = root / "directory-with-fifo"
                directory_with_fifo.mkdir()
                os.mkfifo(directory_with_fifo / "nested.fifo")
                with self.assertRaisesRegex(
                    JavaReducerError,
                    "contain only regular files and directories",
                ):
                    prepare_java_analysis_classpath(
                        source,
                        [str(directory_with_fifo)],
                    )

                fifo = root / "classpath.fifo"
                os.mkfifo(fifo)
                with self.assertRaisesRegex(
                    JavaReducerError,
                    "regular file or directory",
                ):
                    prepare_java_analysis_classpath(source, [str(fifo)])

    def test_classpath_preparation_rejects_unreadable_file_and_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            classpath_file = root / "dependency.jar"
            classpath_file.write_bytes(b"dependency")
            classpath_directory = root / "classes"
            classpath_directory.mkdir()

            cases = [
                (classpath_file, os.R_OK, "classpath file is unreadable"),
                (
                    classpath_directory,
                    os.R_OK | os.X_OK,
                    "classpath directory is unreadable",
                ),
            ]
            for path, mode, message in cases:
                with self.subTest(path=path.name):
                    with mock.patch(
                        "repomin.java.os.access",
                        return_value=False,
                    ) as access:
                        with self.assertRaisesRegex(JavaReducerError, message):
                            prepare_java_analysis_classpath(source, [str(path)])
                        access.assert_called_once_with(path.resolve(), mode)

    def test_partial_attribution_links_local_symbols_with_non_call_error(self) -> None:
        source_text = """\
@MissingAnnotation
final class PartialAttribution {
    static String local(String value, String unused) {
        return value;
    }

    static void call() {
        local("kept", "local-noise");
        Integer.parseInt("1", 10);
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            java_file = root / "PartialAttribution.java"
            java_file.write_text(source_text, encoding="utf-8")
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            groups = [
                target for target in targets if isinstance(target, JavaChangeSet)
            ]
            local = next(
                target
                for target in groups
                if "#local(" in target.label and target.label.endswith(":1")
            )
            self.assertEqual(2, len(local.targets))
            selected = [
                java_file.read_bytes()[target.start : target.end].decode("utf-8")
                for target in local.targets
            ]
            self.assertIn(', "local-noise"', selected)
            self.assertFalse(any("parseInt" in target.label for target in groups))

    def test_call_depth_does_not_leak_to_later_non_call_error(self) -> None:
        source_text = """\
final class CallDepthReset {
    static String local(String value, String unused) {
        return value;
    }

    static void call() {
        local("kept", "noise");
    }

    Missing laterField;
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "CallDepthReset.java").write_text(source_text, encoding="utf-8")
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            linked = next(
                target
                for target in targets
                if isinstance(target, JavaChangeSet)
                and "#local(" in target.label
                and target.label.endswith(":1")
            )
            self.assertEqual(2, len(linked.targets))

    def test_error_typed_overload_disables_all_symbol_groups(self) -> None:
        source_text = """\
final class ErrorTypedOverload {
    static String local(String value, String unused) {
        return value;
    }

    static String overloaded(Missing value, String unused) {
        return String.valueOf(value);
    }

    static String overloaded(String value, String unused) {
        return value;
    }

    static void call() {
        local("kept", "local-noise");
        overloaded(new Missing(), "unsafe-noise");
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ErrorTypedOverload.java").write_text(
                source_text, encoding="utf-8"
            )
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            self.assertFalse(
                any(isinstance(target, JavaChangeSet) for target in targets)
            )
            self.assertIn("parameter", {target.kind for target in targets})
            self.assertIn("argument", {target.kind for target in targets})

    def test_nested_error_type_in_poly_expression_disables_symbol_groups(self) -> None:
        source_text = """\
final class PolyErrorType {
    static String local(String value, String unused) {
        return value;
    }

    static String overloaded(String value, String unused) {
        return value;
    }

    static String overloaded(Object value, Object unused) {
        return String.valueOf(value);
    }

    static void call() {
        local("kept", "local-noise");
        overloaded(true ? "text" : Missing.VALUE, "unsafe-noise");
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "PolyErrorType.java").write_text(source_text, encoding="utf-8")
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            self.assertFalse(
                any(isinstance(target, JavaChangeSet) for target in targets)
            )
            self.assertIn("parameter", {target.kind for target in targets})
            self.assertIn("argument", {target.kind for target in targets})

    def test_error_type_in_type_variable_bound_disables_symbol_groups(self) -> None:
        source_text = """\
final class Known {}

final class GenericBoundError {
    static String local(String value, String unused) {
        return value;
    }

    static <T extends Missing> String overloaded(T value, String unused) {
        return "generic";
    }

    static String overloaded(Object value, Object unused) {
        return "object";
    }

    static void call() {
        local("kept", "local-noise");
        overloaded(new Known(), "unsafe-noise");
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "GenericBoundError.java").write_text(
                source_text, encoding="utf-8"
            )
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            self.assertFalse(
                any(isinstance(target, JavaChangeSet) for target in targets)
            )
            self.assertIn("parameter", {target.kind for target in targets})
            self.assertIn("argument", {target.kind for target in targets})

    def test_nested_declared_error_type_disables_symbol_groups(self) -> None:
        source_text = """\
import java.util.List;

final class NestedDeclaredError {
    static String local(String value, String unused) {
        return value;
    }

    static String unsafe(List<? extends Missing> values, String unused) {
        return String.valueOf(values);
    }

    static void call() {
        local("kept", "local-noise");
        unsafe(null, "unsafe-noise");
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "NestedDeclaredError.java").write_text(
                source_text, encoding="utf-8"
            )
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            self.assertFalse(
                any(isinstance(target, JavaChangeSet) for target in targets)
            )

    def test_error_typed_superclass_disables_symbol_groups(self) -> None:
        source_text = """\
final class HierarchyError extends Missing {
    static String overloaded(Object value, Object unused) {
        return "local";
    }

    static void call() {
        overloaded("kept", "unsafe-noise");
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "HierarchyError.java").write_text(
                source_text, encoding="utf-8"
            )
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            self.assertFalse(
                any(isinstance(target, JavaChangeSet) for target in targets)
            )

    def test_recursive_type_variable_bound_remains_linkable(self) -> None:
        source_text = """\
final class RecursiveTypeBound {
    static <T extends Comparable<T>> T local(T value, String unused) {
        return value;
    }

    static void call() {
        local("kept", "noise");
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            java_file = root / "RecursiveTypeBound.java"
            java_file.write_text(source_text, encoding="utf-8")
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            linked = next(
                target
                for target in targets
                if isinstance(target, JavaChangeSet)
                and "#<T>local(" in target.label
                and target.label.endswith(":1")
            )
            self.assertTrue(_apply_candidate(root, linked))
            completed = subprocess.run(
                ["javac", "-encoding", "UTF-8", str(java_file)],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

    def test_unbounded_wildcard_does_not_disable_symbol_groups(self) -> None:
        source_text = """\
import java.util.List;

final class WildcardSymbols {
    static String local(List<?> values, String unused) {
        return String.valueOf(values.size());
    }

    static void call() {
        local(List.of("kept"), "noise");
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            java_file = root / "WildcardSymbols.java"
            java_file.write_text(source_text, encoding="utf-8")
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            linked = next(
                target
                for target in targets
                if isinstance(target, JavaChangeSet)
                and "#local(" in target.label
                and target.label.endswith(":1")
            )
            self.assertTrue(_apply_candidate(root, linked))
            self.assertIn(
                "local(List<?> values)", java_file.read_text(encoding="utf-8")
            )
            completed = subprocess.run(
                ["javac", "-encoding", "UTF-8", str(java_file)],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

    def test_helper_protocol_keeps_legacy_targets_and_honors_group_blockers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            java_file = root / "Protocol.java"
            java_file.write_bytes(b"abcdef")

            def record(start: int, end: int, label: str, **extra: str) -> str:
                item = {
                    "path": str(java_file),
                    "kind": extra.pop("kind", "coordinated-parameter"),
                    "start": start,
                    "end": end,
                    "label": label,
                }
                item.update(extra)
                return json.dumps(item)

            output = "\n".join(
                [
                    record(0, 1, "legacy", kind="member"),
                    record(1, 2, "linked", replacement="", group="kept", role="declaration"),
                    record(2, 3, "linked", replacement="", group="kept", role="call"),
                    record(3, 4, "blocked", replacement="", group="blocked", role="declaration"),
                    record(4, 5, "blocked", replacement="", group="blocked", role="call"),
                    record(5, 6, "blocked", replacement="", group="blocked", role="blocker"),
                    json.dumps(
                        {
                            "path": "/tmp/outside-repomin-protocol.java",
                            "kind": "member",
                            "start": 0,
                            "end": 1,
                            "label": "outside",
                        }
                    ),
                    "not-json",
                ]
            )
            candidates = _ordered_candidates(list(_parse_targets(root, output)))

            legacy = next(
                target
                for target in candidates
                if not isinstance(target, JavaChangeSet) and target.label == "legacy"
            )
            groups = [
                target for target in candidates if isinstance(target, JavaChangeSet)
            ]
            self.assertEqual(b"\n", legacy.replacement)
            self.assertEqual(["linked"], [target.label for target in groups])
            self.assertEqual(2, len(groups[0].targets))

    def test_unrecoverable_compiler_diagnostics_disable_symbol_groups(self) -> None:
        source_text = """\
final class AmbiguousSymbols {
    static String local(String value, String unused) {
        return value;
    }

    static String ambiguous(String value, String unused) {
        return value;
    }

    static String ambiguous(Integer value, String unused) {
        return String.valueOf(value);
    }

    static void call() {
        local("kept", "noise");
        ambiguous(null, "ambiguous");
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "AmbiguousSymbols.java").write_text(source_text, encoding="utf-8")
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            self.assertFalse(
                any(isinstance(target, JavaChangeSet) for target in targets)
            )
            self.assertIn("parameter", {target.kind for target in targets})
            self.assertIn("argument", {target.kind for target in targets})

    def test_non_call_compiler_error_disables_symbol_groups(self) -> None:
        source_text = """\
final class DuplicateLocal {
    static String local(String value, String unused) {
        return value;
    }

    static void call() {
        int duplicate = 1;
        int duplicate = 2;
        local("kept", "noise");
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "DuplicateLocal.java").write_text(source_text, encoding="utf-8")
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            self.assertFalse(
                any(isinstance(target, JavaChangeSet) for target in targets)
            )
            self.assertIn("parameter", {target.kind for target in targets})

    def test_error_typed_constructor_and_member_reference_disable_groups(self) -> None:
        fixtures = {
            "MissingConstructor.java": """\
final class MissingConstructor {
    static String local(String value, String unused) { return value; }
    static void call() {
        local("kept", "noise");
        new Missing();
    }
}
""",
            "MissingReference.java": """\
import java.util.function.Supplier;

final class MissingReference {
    static String local(String value, String unused) { return value; }
    static void call() {
        local("kept", "noise");
        Supplier<?> supplier = Missing::method;
    }
}
""",
        }
        with _JavaStructureAnalyzer() as analyzer:
            for filename, source_text in fixtures.items():
                with self.subTest(filename=filename):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        (root / filename).write_text(source_text, encoding="utf-8")
                        targets = analyzer.analyze(root)

                    self.assertFalse(
                        any(isinstance(target, JavaChangeSet) for target in targets)
                    )

    def test_anonymous_subclass_blocks_base_constructor_groups(self) -> None:
        source_text = """\
class AnonymousBase {
    AnonymousBase(String value, int unused) {}

    static void call() {
        new AnonymousBase("ordinary", 1);
        new AnonymousBase("anonymous", 2) {};
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "AnonymousBase.java").write_text(source_text, encoding="utf-8")
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            labels = {
                target.label
                for target in targets
                if isinstance(target, JavaChangeSet)
            }
            self.assertFalse(any("#AnonymousBase(" in label for label in labels))
            self.assertIn("parameter", {target.kind for target in targets})
            self.assertIn("argument", {target.kind for target in targets})

    def test_record_constructors_do_not_form_symbol_groups(self) -> None:
        version = subprocess.run(
            ["javac", "-version"],
            text=True,
            capture_output=True,
            timeout=10,
        )
        version_text = (version.stdout or version.stderr).strip().split()[-1]
        if int(version_text.split(".")[0]) < 16:
            self.skipTest("records require JDK 16 or newer")

        source_text = """\
record RecordSymbols(String value, int number) {
    RecordSymbols(String value, int number, String unused) {
        this(value, number);
    }

    static void call() {
        new RecordSymbols("kept", 1, "noise");
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "RecordSymbols.java").write_text(source_text, encoding="utf-8")
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            labels = {
                target.label
                for target in targets
                if isinstance(target, JavaChangeSet)
            }
            self.assertFalse(any("#RecordSymbols(" in label for label in labels))
            self.assertIn("parameter", {target.kind for target in targets})
            self.assertIn("argument", {target.kind for target in targets})

    def test_parameter_references_and_method_references_block_symbol_groups(self) -> None:
        source_text = """\
import java.util.function.BiFunction;

final class BlockedSymbols {
    static String used(String value, String unused) {
        return value + unused;
    }

    static String referenced(String value, String unused) {
        return value;
    }

    static void calls() {
        used("a", "b");
        referenced("a", "b");
        BiFunction<String, String, String> function = BlockedSymbols::referenced;
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "BlockedSymbols.java").write_text(source_text, encoding="utf-8")
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            labels = {
                target.label
                for target in targets
                if isinstance(target, JavaChangeSet)
            }
            self.assertFalse(any("#used(" in label for label in labels))
            self.assertFalse(any("#referenced(" in label for label in labels))
            self.assertIn("parameter", {target.kind for target in targets})
            self.assertIn("argument", {target.kind for target in targets})

    def test_symbol_analysis_crosses_the_previous_hundred_file_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "A000Helper.java").write_text(
                "final class A000Helper { "
                "static String trim(String value, String unused) { return value; } "
                "}\n",
                encoding="utf-8",
            )
            for index in range(99):
                name = "M%03d" % index
                (root / (name + ".java")).write_text(
                    "final class %s {}\n" % name,
                    encoding="utf-8",
                )
            (root / "Z999Caller.java").write_text(
                "final class Z999Caller { "
                "static String call() { return A000Helper.trim(\"kept\", \"noise\"); } "
                "}\n",
                encoding="utf-8",
            )

            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            linked = next(
                target
                for target in targets
                if isinstance(target, JavaChangeSet)
                and "#trim(" in target.label
                and target.label.endswith(":1")
            )
            self.assertEqual(2, len(linked.targets))
            self.assertEqual(
                {Path("A000Helper.java"), Path("Z999Caller.java")},
                {target.path for target in linked.targets},
            )

    def test_external_generic_overrides_and_prospective_collisions_are_blocked(
        self,
    ) -> None:
        external_source = """\
package ext;

public class GenericBase<T> {
    public T inherited(T value, String unused) {
        return value;
    }

    public T prospective(T value) {
        return value;
    }
}
"""
        subject_source = """\
import ext.GenericBase;

class ExternalGenericCollision extends GenericBase<String> {
    @Override
    public final String inherited(String value, String unused) {
        return value;
    }

    final String prospective(String value, String unused) {
        return value;
    }

    final String safe(String value, String unused) {
        return value;
    }

    static void call() {
        ExternalGenericCollision host = new ExternalGenericCollision();
        host.inherited("kept", "override-noise");
        host.prospective("kept", "prospective-noise");
        host.safe("kept", "safe-noise");
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            external_java = root / "external-src" / "ext" / "GenericBase.java"
            external_java.parent.mkdir(parents=True)
            external_java.write_text(external_source, encoding="utf-8")
            external_classes = root / "external-classes"
            external_classes.mkdir()
            completed = subprocess.run(
                [
                    "javac",
                    "-encoding",
                    "UTF-8",
                    "-d",
                    str(external_classes),
                    str(external_java),
                ],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

            source = root / "subject"
            source.mkdir()
            java_file = source / "ExternalGenericCollision.java"
            java_file.write_text(subject_source, encoding="utf-8")
            classpath = prepare_java_analysis_classpath(
                source,
                [str(external_classes)],
            )
            with _JavaStructureAnalyzer(classpath) as analyzer:
                targets = analyzer.analyze(source)

            groups = [
                target for target in targets if isinstance(target, JavaChangeSet)
            ]
            self.assertEqual(
                [
                    (
                        "ExternalGenericCollision#safe"
                        "(java.lang.String,java.lang.String):1"
                    )
                ],
                [target.label for target in groups],
            )
            self.assertEqual(2, len(groups[0].targets))
            self.assertFalse(any("#inherited(" in target.label for target in groups))
            self.assertFalse(any("#prospective(" in target.label for target in groups))

            completed = subprocess.run(
                [
                    "javac",
                    "-encoding",
                    "UTF-8",
                    "-cp",
                    str(external_classes),
                    str(java_file),
                ],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

    def test_same_class_prospective_overload_collision_is_blocked(self) -> None:
        source_text = """\
class LocalCollision {
    final String choose(String value) {
        return value;
    }

    final String choose(String value, String unused) {
        return value;
    }

    final String safe(String value, String unused) {
        return value;
    }

    static void call() {
        LocalCollision host = new LocalCollision();
        host.choose("kept", "collision-noise");
        host.safe("kept", "safe-noise");
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            java_file = root / "LocalCollision.java"
            java_file.write_text(source_text, encoding="utf-8")
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            groups = [
                target for target in targets if isinstance(target, JavaChangeSet)
            ]
            self.assertEqual(
                ["LocalCollision#safe(java.lang.String,java.lang.String):1"],
                [target.label for target in groups],
            )
            self.assertEqual(2, len(groups[0].targets))
            self.assertFalse(any("#choose(" in target.label for target in groups))
            self.assertTrue(_apply_candidate(root, groups[0]))
            completed = subprocess.run(
                ["javac", "-encoding", "UTF-8", str(java_file)],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

    def test_generic_bridge_prospective_collision_is_blocked(self) -> None:
        source_text = """\
interface GenericContract<T> {
    void accept(T value);
}

class StringBase {
    public void accept(String value) {}
}

final class BridgeChild extends StringBase implements GenericContract<String> {
    public final void accept(Object value, int unused) {
        System.out.println(value);
    }

    String safe(String value, String unused) {
        return value;
    }

    static void call() {
        BridgeChild child = new BridgeChild();
        child.accept(new Object(), 1);
        child.safe("kept", "safe-noise");
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            java_file = root / "BridgeChild.java"
            java_file.write_text(source_text, encoding="utf-8")
            completed = subprocess.run(
                ["javac", "-encoding", "UTF-8", str(java_file)],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            groups = [
                target for target in targets if isinstance(target, JavaChangeSet)
            ]
            self.assertEqual(
                ["BridgeChild#safe(java.lang.String,java.lang.String):1"],
                [target.label for target in groups],
            )
            self.assertEqual(2, len(groups[0].targets))
            self.assertFalse(any("#accept(" in target.label for target in groups))

    def test_closed_source_override_family_reduces_declarations_and_dispatch_calls(
        self,
    ) -> None:
        source_text = """\
class Base {
    String render(String value, String unused) {
        return "B:" + value;
    }
}

final class Child extends Base {
    @Override
    String render(String value, String unused) {
        return "C:" + value;
    }
}

class ClosedOverrideCalls {
    static String call() {
        Base first = new Child();
        return first.render("kept", "base-noise")
                + "|" + new Child().render("kept", "child-noise");
    }

    public static void main(String[] args) {
        if (!"C:kept|C:kept".equals(call())) {
            throw new AssertionError(call());
        }
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            java_file = root / "ClosedOverrideCalls.java"
            java_file.write_text(source_text, encoding="utf-8")
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            groups = [
                target for target in targets if isinstance(target, JavaChangeSet)
            ]
            self.assertEqual(
                ["Base#render(java.lang.String,java.lang.String):1"],
                [target.label for target in groups],
            )
            self.assertEqual(4, len(groups[0].targets))
            self.assertEqual(
                {Path("ClosedOverrideCalls.java")},
                {target.path for target in groups[0].targets},
            )
            self.assertTrue(_apply_candidate(root, groups[0]))
            reduced = java_file.read_text(encoding="utf-8")
            self.assertIn("String render(String value)", reduced)
            self.assertIn('first.render("kept")', reduced)
            self.assertIn('new Child().render("kept")', reduced)

            classes = root / "classes"
            classes.mkdir()
            completed = subprocess.run(
                ["javac", "-encoding", "UTF-8", "-d", str(classes), str(java_file)],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            completed = subprocess.run(
                ["java", "-cp", str(classes), "ClosedOverrideCalls"],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

    def test_private_ancestor_does_not_create_prospective_collision(self) -> None:
        source_text = """\
class PrivateAncestor {
    private String hidden(String value) {
        return value;
    }
}

class PrivateChild extends PrivateAncestor {
    final String hidden(String value, int unused) {
        return value;
    }

    static void call() {
        new PrivateChild().hidden("kept", 1);
    }
}

class SameClassPrivateCollision {
    private String hidden(String value) {
        return value;
    }

    final String hidden(String value, int unused) {
        return value;
    }

    static void call() {
        new SameClassPrivateCollision().hidden("kept", 1);
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            java_file = root / "PrivateCollision.java"
            java_file.write_text(source_text, encoding="utf-8")
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            groups = [
                target for target in targets if isinstance(target, JavaChangeSet)
            ]
            self.assertEqual(
                ["PrivateChild#hidden(java.lang.String,int):1"],
                [target.label for target in groups],
            )
            self.assertEqual(2, len(groups[0].targets))
            selected = [
                java_file.read_bytes()[target.start : target.end].decode("utf-8")
                for target in groups[0].targets
            ]
            self.assertIn(", int unused", selected)
            self.assertIn(", 1", selected)
            self.assertFalse(
                any(
                    target.label.startswith("SameClassPrivateCollision#hidden(")
                    for target in groups
                )
            )
            self.assertTrue(_apply_candidate(root, groups[0]))
            reduced = java_file.read_text(encoding="utf-8")
            self.assertIn("hidden(String value)", reduced)
            self.assertIn('new PrivateChild().hidden("kept")', reduced)
            self.assertIn(
                'new SameClassPrivateCollision().hidden("kept", 1)',
                reduced,
            )
            completed = subprocess.run(
                ["javac", "-encoding", "UTF-8", str(java_file)],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

    def test_closed_override_family_links_across_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Base.java").write_text(
                """\
class Base {
    String render(String value, String unused) {
        return "B:" + value;
    }
}
""",
                encoding="utf-8",
            )
            (root / "Child.java").write_text(
                """\
final class Child extends Base {
    @Override
    String render(String value, String unused) {
        return "C:" + value;
    }
}
""",
                encoding="utf-8",
            )
            caller = root / "CrossFileCalls.java"
            caller.write_text(
                """\
public class CrossFileCalls {
    static String call() {
        Base first = new Child();
        return first.render("kept", "base-noise")
                + "|" + new Child().render("kept", "child-noise");
    }

    public static void main(String[] args) {
        if (!"C:kept|C:kept".equals(call())) {
            throw new AssertionError(call());
        }
    }
}
""",
                encoding="utf-8",
            )
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            groups = [
                target for target in targets if isinstance(target, JavaChangeSet)
            ]
            self.assertEqual(
                ["Base#render(java.lang.String,java.lang.String):1"],
                [target.label for target in groups],
            )
            self.assertEqual(4, len(groups[0].targets))
            self.assertEqual(
                {Path("Base.java"), Path("Child.java"), Path("CrossFileCalls.java")},
                {target.path for target in groups[0].targets},
            )
            self.assertTrue(_apply_candidate(root, groups[0]))

            classes = root / "classes"
            classes.mkdir()
            completed = subprocess.run(
                [
                    "javac",
                    "-encoding",
                    "UTF-8",
                    "-d",
                    str(classes),
                    *[str(path) for path in sorted(root.glob("*.java"))],
                ],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            completed = subprocess.run(
                ["java", "-cp", str(classes), "CrossFileCalls"],
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

    def test_helper_protocol_stays_utf8_with_ascii_default_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / "Definition.java"
            caller = root / "Caller_\u4e2d.java"
            helper.write_text(
                "final class Definition { "
                "static String local(String value, String unused) { return value; } "
                "}\n",
                encoding="utf-8",
            )
            caller.write_text(
                "final class Caller { "
                "static String call() { return Definition.local(\"kept\", \"noise\"); } "
                "}\n",
                encoding="utf-8",
            )

            with _JavaStructureAnalyzer() as analyzer:
                with mock.patch.dict(
                    os.environ,
                    {"JAVA_TOOL_OPTIONS": "-Dfile.encoding=US-ASCII"},
                ):
                    targets = analyzer.analyze(root)

            linked = next(
                target
                for target in targets
                if isinstance(target, JavaChangeSet)
                and "#local(" in target.label
                and target.label.endswith(":1")
            )
            self.assertEqual(
                {Path("Definition.java"), Path("Caller_\u4e2d.java")},
                {target.path for target in linked.targets},
            )

    def test_java_replacement_targets_use_utf8_offsets_and_stale_hashes(self) -> None:
        source_text = """\
// \U0001f600 supplementary character before every emitted range
class UnicodeTarget {
    @Deprecated void run(String first, String second) {
        throw new RuntimeException(true ? "失败" : "unused");
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            java_file = root / "UnicodeTarget.java"
            java_file.write_text(source_text, encoding="utf-8")
            with _JavaStructureAnalyzer() as analyzer:
                targets = analyzer.analyze(root)

            self.assertIn("annotation", {target.kind for target in targets})
            self.assertIn("parameter", {target.kind for target in targets})
            self.assertIn("expression", {target.kind for target in targets})
            conditional = next(
                target
                for target in targets
                if target.kind == "expression"
                and target.label == "conditional:true"
            )
            self.assertEqual('"失败"', conditional.replacement.decode("utf-8"))
            parameter = next(
                target
                for target in targets
                if target.kind == "parameter" and target.label.endswith(":0")
            )
            self.assertTrue(_apply_target(root, parameter))
            reduced = java_file.read_text(encoding="utf-8")
            self.assertIn("void run(String second)", reduced)
            self.assertFalse(_apply_target(root, parameter))

    def test_protocol_preserves_unicode_line_separator_and_caches_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            java_file = root / "Line\u2028Separator.java"
            java_file.write_bytes(b"abcdef")
            output = "\n".join(
                json.dumps(
                    {
                        "path": str(java_file),
                        "kind": "member",
                        "start": start,
                        "end": start + 1,
                        "label": "item-%d" % start,
                    },
                    ensure_ascii=False,
                )
                for start in (0, 1)
            )

            parsed = iter(_parse_targets(root, output))
            records = [next(parsed)]
            java_file.write_bytes(b"x")
            records.extend(parsed)

            self.assertEqual(2, len(records))
            self.assertEqual(["item-0", "item-1"], [item.target.label for item in records])

    def test_multi_file_write_failure_rolls_back_attempted_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "First.java"
            second = root / "Second.java"
            first.write_bytes(b"abc")
            second.write_bytes(b"def")

            def target(path: Path, data: bytes) -> JavaTarget:
                return JavaTarget(
                    path=path.relative_to(root),
                    kind="coordinated-parameter",
                    start=0,
                    end=1,
                    label=path.name,
                    content_hash=hashlib.sha256(data[:1]).hexdigest(),
                    replacement=b"",
                )

            change_set = JavaChangeSet(
                kind="coordinated-parameter",
                label="rollback",
                targets=(target(first, b"abc"), target(second, b"def")),
            )
            original_write_bytes = Path.write_bytes
            failed = []

            def failing_write_bytes(path: Path, data: bytes) -> int:
                if path == second and not failed:
                    failed.append(path)
                    original_write_bytes(path, b"partially-written")
                    raise OSError("simulated write failure")
                return original_write_bytes(path, data)

            with mock.patch.object(Path, "write_bytes", new=failing_write_bytes):
                self.assertFalse(_apply_candidate(root, change_set))

            self.assertEqual(b"abc", first.read_bytes())
            self.assertEqual(b"def", second.read_bytes())

    def test_multi_file_rollback_failure_is_not_reported_as_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "First.java"
            second = root / "Second.java"
            first.write_bytes(b"abc")
            second.write_bytes(b"def")

            def target(path: Path, data: bytes) -> JavaTarget:
                return JavaTarget(
                    path=path.relative_to(root),
                    kind="coordinated-parameter",
                    start=0,
                    end=1,
                    label=path.name,
                    content_hash=hashlib.sha256(data[:1]).hexdigest(),
                    replacement=b"",
                )

            change_set = JavaChangeSet(
                kind="coordinated-parameter",
                label="rollback-error",
                targets=(target(first, b"abc"), target(second, b"def")),
            )
            original_write_bytes = Path.write_bytes
            transformed_write_failed = []

            def failing_write_bytes(path: Path, data: bytes) -> int:
                if path == second and not transformed_write_failed:
                    transformed_write_failed.append(path)
                    raise OSError("simulated transformed write failure")
                if path == first and transformed_write_failed and data == b"abc":
                    raise OSError("simulated rollback failure")
                return original_write_bytes(path, data)

            with mock.patch.object(Path, "write_bytes", new=failing_write_bytes):
                with self.assertRaisesRegex(JavaReducerError, "failed to roll back"):
                    _apply_candidate(root, change_set)


if __name__ == "__main__":
    unittest.main()
