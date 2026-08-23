import hashlib
import tempfile
import unittest
from pathlib import Path

from repomin.text_reducer import (
    TextLineTarget,
    _describe_targets,
    _discover_targets,
    _target_location,
)


class TextReducerDiscoveryTest(unittest.TestCase):
    def test_discover_targets_produces_ordered_non_overlapping_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
            targets = _discover_targets(root, ["data.txt"])
            self.assertEqual(3, len(targets))
            self.assertEqual([0, 6, 11], [target.start for target in targets])
            self.assertEqual([6, 11, 17], [target.end for target in targets])
            self.assertEqual(
                [
                    hashlib.sha256(line.encode("utf-8")).hexdigest()
                    for line in ("alpha\n", "beta\n", "gamma\n")
                ],
                [target.content_hash for target in targets],
            )

    def test_discover_targets_skips_missing_binary_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data.txt").write_text("a\nb\n", encoding="utf-8")
            (root / "binary.dat").write_bytes(b"\xff\xfe\xfa")
            self.assertEqual(2, len(_discover_targets(root, ["data.txt", "missing.txt", "binary.dat"])))

    def test_describe_and_location_helpers(self) -> None:
        target = TextLineTarget(
            Path("data.txt"),
            0,
            6,
            "remove line 1 of data.txt",
            "a" * 64,
        )
        self.assertEqual((Path("data.txt"), 0, 6), _target_location(target))
        self.assertEqual("remove 1 line(s) from data.txt", _describe_targets([target]))


if __name__ == "__main__":
    unittest.main()
