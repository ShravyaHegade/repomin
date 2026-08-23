import unittest
from pathlib import PurePosixPath

from repomin.gitignore import GitignoreError, GitignoreMatcher


class GitignoreMatcherTest(unittest.TestCase):
    def _match(self, text: str, *paths: str) -> dict:
        matcher = GitignoreMatcher.from_text(text, "test")
        return {
            path: matcher.matches(PurePosixPath(path))
            for path in paths
        }

    def test_comments_and_blank_lines_are_ignored(self) -> None:
        matcher = GitignoreMatcher.from_text("# comment\n\n# another\n", "test")
        self.assertEqual([], list(matcher.rules))

    def test_unanchored_basename_glob_matches_at_any_depth(self) -> None:
        self.assertEqual(
            {
                "a.log": True,
                "dir/a.log": True,
                "deep/nested/a.log": True,
                "a.txt": False,
            },
            self._match("*.log", "a.log", "dir/a.log", "deep/nested/a.log", "a.txt"),
        )

    def test_double_star_crosses_directory_separators(self) -> None:
        self.assertEqual(
            {
                "cache": False,
                "cache/x": True,
                "x/cache/y": False,
            },
            self._match(
                "cache/**",
                "cache",
                "cache/x",
                "x/cache/y",
            ),
        )

    def test_anchored_directory_rule_matches_descendants(self) -> None:
        self.assertEqual(
            {
                "build": True,
                "build/out.bin": True,
                "build/a/b.txt": True,
                "src/build/out.bin": False,
            },
            self._match(
                "/build/",
                "build",
                "build/out.bin",
                "build/a/b.txt",
                "src/build/out.bin",
            ),
        )

    def test_negation_reincludes_only_an_earlier_rule(self) -> None:
        self.assertEqual(
            {
                "build": True,
                "build/keep.txt": False,
                "build/drop.txt": True,
            },
            self._match(
                "build/\n!build/keep.txt",
                "build",
                "build/keep.txt",
                "build/drop.txt",
            ),
        )

    def test_question_mark_matches_one_non_separator_character(self) -> None:
        self.assertEqual(
            {"file1.log": True, "file12.log": False},
            self._match("file?.log", "file1.log", "file12.log"),
        )

    def test_character_class_is_translated(self) -> None:
        self.assertEqual(
            {"file1.log": True, "file2.log": True, "file3.log": False},
            self._match("file[12].log", "file1.log", "file2.log", "file3.log"),
        )

    def test_character_class_negation_markers_are_supported(self) -> None:
        for pattern in ("file[!12].log", "file[^12].log"):
            with self.subTest(pattern=pattern):
                self.assertEqual(
                    {"file1.log": False, "file2.log": False, "file3.log": True},
                    self._match(pattern, "file1.log", "file2.log", "file3.log"),
                )

    def test_unterminated_character_class_is_rejected(self) -> None:
        with self.assertRaises(GitignoreError):
            GitignoreMatcher.from_text("file[12.log", "test")


if __name__ == "__main__":
    unittest.main()
