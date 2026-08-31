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

    def test_double_star_directory_segment_can_match_zero_directories(self) -> None:
        self.assertEqual(
            {
                "foo/bar": True,
                "foo/x/bar": True,
                "foo/x/y/bar": True,
                "bar": False,
                "other/bar": False,
            },
            self._match(
                "foo/**/bar",
                "foo/bar",
                "foo/x/bar",
                "foo/x/y/bar",
                "bar",
                "other/bar",
            ),
        )
        self.assertEqual(
            {"bar": True, "nested/bar": True, "bar.txt": False},
            self._match("**/bar", "bar", "nested/bar", "bar.txt"),
        )

    def test_embedded_double_star_stays_within_one_path_segment(self) -> None:
        self.assertEqual(
            {
                "ab.txt": True,
                "a/b.txt": False,
            },
            self._match("a**b.txt", "ab.txt", "a/b.txt"),
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

    def test_directory_only_rule_does_not_match_same_named_file(self) -> None:
        matcher = GitignoreMatcher.from_text("generated/", "test")
        self.assertFalse(
            matcher.matches(PurePosixPath("generated"), is_directory=False)
        )
        self.assertTrue(
            matcher.matches(PurePosixPath("generated"), is_directory=True)
        )
        self.assertTrue(
            matcher.matches(PurePosixPath("generated/file.txt"), is_directory=False)
        )

    def test_wildcard_directory_rule_distinguishes_target_file_from_descendant(self) -> None:
        matcher = GitignoreMatcher.from_text("foo/*/bar/", "test")
        self.assertFalse(
            matcher.matches(PurePosixPath("foo/x/bar"), is_directory=False)
        )
        self.assertTrue(
            matcher.matches(PurePosixPath("foo/x/bar"), is_directory=True)
        )
        self.assertTrue(
            matcher.matches(PurePosixPath("foo/x/bar/file.txt"), is_directory=False)
        )

    def test_double_star_directory_rule_keeps_descendant_files_ignored(self) -> None:
        matcher = GitignoreMatcher.from_text("foo/**/", "test")
        self.assertFalse(
            matcher.matches(PurePosixPath("foo/x"), is_directory=False)
        )
        self.assertTrue(
            matcher.matches(PurePosixPath("foo/x"), is_directory=True)
        )
        self.assertTrue(
            matcher.matches(PurePosixPath("foo/x/file.txt"), is_directory=False)
        )

    def test_directory_only_negation_does_not_restore_same_named_file(self) -> None:
        matcher = GitignoreMatcher.from_text("*\n!generated/\n", "test")
        self.assertTrue(
            matcher.matches(PurePosixPath("generated"), is_directory=False)
        )
        self.assertFalse(
            matcher.matches(PurePosixPath("generated"), is_directory=True)
        )

    def test_bare_directory_rule_matches_descendants(self) -> None:
        self.assertEqual(
            {
                "generated": True,
                "generated/package.json": True,
                "nested/generated/package.json": True,
                "generated.txt": False,
            },
            self._match(
                "generated",
                "generated",
                "generated/package.json",
                "nested/generated/package.json",
                "generated.txt",
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

    def test_double_star_negation_remains_reachable_at_deeper_depth(self) -> None:
        matcher = GitignoreMatcher.from_text(
            "foo/\n!foo/**/keep.txt\n",
            "test",
        )
        self.assertTrue(matcher.may_reinclude_descendant(PurePosixPath("foo")))
        self.assertTrue(
            matcher.may_reinclude_descendant(PurePosixPath("foo/x/y"))
        )
        self.assertFalse(
            matcher.may_reinclude_descendant(PurePosixPath("other/x/y"))
        )
        self.assertFalse(
            matcher.matches(
                PurePosixPath("foo/x/y/keep.txt"),
                is_directory=False,
            )
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
