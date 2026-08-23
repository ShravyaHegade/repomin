import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from repomin.model import FailureSpec, ReductionStats
from repomin.oracle import CommandRunner, FailureOracle
from repomin.ruby_manifest import RubyManifestReducer, _discover_targets, _remove_target
from repomin.session import ReductionSession


GEMFILE = """\
source "https://rubygems.org"
ruby "3.2.0"

gem "required", "~> 1.0"
gem "unused", "~> 2.0"
# gem "comment-only"
text = "gem \\\"string-only\\\""

group :development do
  gem "group-unused"
end

gem("paren-unused")
gem(
  "multiline-unused"
)
gem "block-unused" do
  puts "side effect"
end

gemspec
"""


class RubyManifestReducerTest(unittest.TestCase):
    def test_discovers_only_complete_gem_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Gemfile").write_text(GEMFILE, encoding="utf-8")
            targets = _discover_targets(root)
            labels = {target.label for target in targets}
            self.assertEqual(
                {"gem required", "gem unused", "gem group-unused", "gem paren-unused"},
                labels,
            )
            self.assertFalse(any("comment" in label or "string" in label for label in labels))

    def test_removals_keep_gemfile_ruby_parseable(self) -> None:
        ruby = shutil.which("ruby")
        if ruby is None:
            self.skipTest("ruby runtime is not installed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "Gemfile"
            for label in ("gem required", "gem unused", "gem paren-unused"):
                path.write_text(GEMFILE, encoding="utf-8")
                target = next(item for item in _discover_targets(root) if item.label == label)
                self.assertTrue(_remove_target(root, target), label)
                result = subprocess.run(
                    [ruby, "-c", "Gemfile"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, result.returncode, result.stderr)

    def test_multiline_and_block_calls_are_left_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "Gemfile"
            path.write_text(GEMFILE, encoding="utf-8")
            targets = _discover_targets(root)
            self.assertNotIn("gem multiline-unused", {item.label for item in targets})
            self.assertNotIn("gem block-unused", {item.label for item in targets})
            self.assertIn("gem(\n", path.read_text(encoding="utf-8"))

    def test_stale_hash_rejects_without_modifying_gemfile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "Gemfile"
            path.write_text(GEMFILE, encoding="utf-8")
            target = next(item for item in _discover_targets(root) if item.label == "gem unused")
            shifted = "# shifted\n" + GEMFILE
            path.write_text(shifted, encoding="utf-8")
            self.assertFalse(_remove_target(root, target))
            self.assertEqual(shifted, path.read_text(encoding="utf-8"))

    def test_gems_rb_alone_makes_adapter_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "gems.rb").write_text('gem "required"\n', encoding="utf-8")
            session = ReductionSession(
                root,
                FailureOracle(
                    CommandRunner("python3 -c 'raise SystemExit(1)'", timeout_seconds=5),
                    FailureSpec(None, exit_code=1),
                ),
                ReductionStats(source_files=1, source_bytes=0),
            )
            try:
                self.assertTrue(RubyManifestReducer(session).is_applicable())
            finally:
                session.close()

    def test_reducer_preserves_required_gem(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            (source / "Gemfile").write_text(GEMFILE, encoding="utf-8")
            (source / "reproduce.rb").write_text(
                "text = File.read('Gemfile')\n"
                "unless text.include?('gem \"required\"')\n"
                "  puts 'DIFFERENT_FAILURE'\n"
                "  exit 2\n"
                "end\n"
                "puts 'ORIGINAL_FAILURE'\n"
                "exit 1\n",
                encoding="utf-8",
            )
            session = ReductionSession(
                source,
                FailureOracle(
                    CommandRunner("ruby reproduce.rb", timeout_seconds=5),
                    FailureSpec("ORIGINAL_FAILURE"),
                ),
                ReductionStats(source_files=2, source_bytes=0),
            )
            try:
                session.verify_baseline(1)
                reducer = RubyManifestReducer(session)
                self.assertTrue(reducer.is_applicable())
                self.assertTrue(reducer.reduce())
                reduced = (session.current / "Gemfile").read_text(encoding="utf-8")
                self.assertIn('gem "required"', reduced)
                self.assertNotIn('gem "unused"', reduced)
                self.assertTrue(session.oracle.accepts(session.run_current()))
            finally:
                session.close()


if __name__ == "__main__":
    unittest.main()
