import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest


_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_docs.py"
_SPEC = importlib.util.spec_from_file_location("repomin_documentation_check", _PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("could not load documentation check utility")
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)


class DocumentationCheckTest(unittest.TestCase):
    def _file(self, root: Path, name: str = "README.md") -> Path:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def test_valid_utf8_lf_and_fences_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._file(Path(directory))
            path.write_bytes(
                "# Example\n\n```sh\nprintf 'ok\\n'\n```\n\n~~~python\npass\n~~~\n".encode(
                    "utf-8"
                )
            )

            self.assertEqual([], _MODULE.check_markdown_file(path))

    def test_invalid_utf8_is_reported_without_decoding_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._file(Path(directory))
            path.write_bytes(b"# title\ninvalid: \xff\n")

            issues = _MODULE.check_markdown_file(path)

            self.assertEqual(["utf8"], [issue.rule for issue in issues])
            self.assertEqual(2, issues[0].line)
            self.assertIn("not valid UTF-8", issues[0].message)

    def test_crlf_and_bare_cr_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._file(Path(directory))
            path.write_bytes(b"# title\r\nline one\rline two\n")

            issues = _MODULE.check_markdown_file(path)

            self.assertEqual(["line-endings"], [issue.rule for issue in issues])
            self.assertEqual(1, issues[0].line)
            self.assertIn("CRLF", issues[0].message)
            self.assertIn("bare CR", issues[0].message)

    def test_unclosed_fence_reports_opening_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._file(Path(directory))
            path.write_bytes(
                "# title\n\n  ```json\n{\"ok\": true}\n".encode("utf-8")
            )

            issues = _MODULE.check_markdown_file(path)

            self.assertEqual(["markdown-fence"], [issue.rule for issue in issues])
            self.assertEqual(3, issues[0].line)
            self.assertIn("```", issues[0].message)

    def test_fence_closer_must_match_marker_and_be_long_enough(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._file(Path(directory))
            path.write_bytes("```python\n~~~\n``\n".encode("utf-8"))

            issues = _MODULE.check_markdown_file(path)

            self.assertEqual(1, len(issues))
            self.assertEqual(1, issues[0].line)

    def test_indented_content_and_ignored_directories_are_not_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root).write_bytes(b"    ```\n")
            self._file(root / ".venv").write_bytes(b"```\n")
            self._file(root / "docs").write_bytes(b"ok\n")

            files = _MODULE.find_markdown_files(root)
            issues = _MODULE.check_tree(root)

            self.assertEqual(
                [Path(directory) / "README.md", Path(directory) / "docs" / "README.md"],
                list(files),
            )
            self.assertEqual([], issues)

    def test_cli_returns_failure_and_prints_relative_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root, "docs/README.md").write_bytes(b"```\r\n")
            output = io.StringIO()
            errors = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                status = _MODULE.main(["--root", str(root)])

            self.assertEqual(1, status)
            self.assertIn("docs/README.md:1", errors.getvalue())
            self.assertIn("line-endings", errors.getvalue())
            self.assertIn("markdown-fence", errors.getvalue())
            self.assertEqual("", output.getvalue())


if __name__ == "__main__":
    unittest.main()
