from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from repomin.batching import try_interval_batches
from repomin.session import ReductionSession
from repomin.text_edits import remove_text_targets


@dataclass(frozen=True)
class RubyManifestTarget:
    path: Path
    category: str
    start: int
    end: int
    label: str
    content_hash: str


class RubyManifestReducer:
    """Reduce complete, single-line gem declarations in Bundler manifests."""

    def __init__(self, session: ReductionSession) -> None:
        self.session = session

    def is_applicable(self) -> bool:
        return bool(_gemfiles(self.session.current))

    def reduce(self) -> bool:
        with self.session.measure_phase("ruby-manifest"):
            accepted_before = self.session.stats.accepted
            while True:
                targets = _discover_targets(self.session.current)
                if not try_interval_batches(
                    self.session,
                    "ruby-manifest",
                    targets,
                    _target_location,
                    _describe_targets,
                    _remove_targets,
                ):
                    break
            return self.session.stats.accepted > accepted_before


def _gemfiles(root: Path) -> List[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and (
            path.name in {"Gemfile", "gems.rb"}
            or (path.name.startswith("Gemfile.") and path.name != "Gemfile.lock")
        )
    )


def _discover_targets(root: Path) -> List[RubyManifestTarget]:
    root = root.resolve()
    targets: List[RubyManifestTarget] = []
    for path in _gemfiles(root):
        try:
            text = _read_text(path)
        except (OSError, UnicodeDecodeError):
            continue
        for category, start, end, label in _gem_lines(text):
            targets.append(
                _target(
                    path.relative_to(root), category, start, end, label, text
                )
            )
    return sorted(
        targets,
        key=lambda item: (item.path.as_posix(), item.start),
    )


def _gem_lines(text: str) -> List[Tuple[str, int, int, str]]:
    lines = text.splitlines(keepends=True)
    results: List[Tuple[str, int, int, str]] = []
    offset = 0
    for line in lines:
        start = offset
        end = offset + len(line)
        offset = end
        raw = line.rstrip("\r\n")
        stripped = raw.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        if not _starts_with_gem(stripped):
            continue
        if not _ruby_line_is_complete(stripped):
            continue
        label = _gem_label(stripped[3:])
        if label is not None:
            results.append(("gem", start, end, label))
    return results


def _starts_with_gem(line: str) -> bool:
    if not line.startswith("gem"):
        return False
    return len(line) == 3 or not (line[3].isalnum() or line[3] in "_?!")


def _gem_label(rest: str) -> Optional[str]:
    index = 0
    while index < len(rest) and rest[index].isspace():
        index += 1
    if index < len(rest) and rest[index] == "(":
        index += 1
        while index < len(rest) and rest[index].isspace():
            index += 1
    if index >= len(rest) or rest[index] not in {"'", '"'}:
        return None
    quote = rest[index]
    index += 1
    value: List[str] = []
    escaped = False
    while index < len(rest):
        char = rest[index]
        index += 1
        if escaped:
            value.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == quote:
            return "gem %s" % "".join(value)
        else:
            value.append(char)
    return None


def _ruby_line_is_complete(line: str) -> bool:
    depths = {"(": 0, "[": 0, "{": 0}
    matching = {")": "(",
        "]": "[",
        "}": "{",
    }
    quote: Optional[str] = None
    escaped = False
    token: List[str] = []
    tokens: List[str] = []
    index = 0
    while index < len(line):
        char = line[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char == "#":
            break
        if char in {"'", '"', "`"}:
            if token:
                tokens.append("".join(token))
                token.clear()
            quote = char
            index += 1
            continue
        if char.isalnum() or char in "_?!":
            token.append(char)
            index += 1
            continue
        if token:
            tokens.append("".join(token))
            token.clear()
        if char in depths:
            depths[char] += 1
        elif char in matching:
            opener = matching[char]
            depths[opener] -= 1
            if depths[opener] < 0:
                return False
        elif char == ";":
            return False
        index += 1
    if token:
        tokens.append("".join(token))
    if quote is not None or any(value != 0 for value in depths.values()):
        return False
    if line.rstrip().endswith("\\") or "do" in tokens:
        return False
    return True


def _target(
    path: Path,
    category: str,
    start: int,
    end: int,
    label: str,
    text: str,
) -> RubyManifestTarget:
    selected = text[start:end].encode("utf-8")
    return RubyManifestTarget(
        path=path,
        category=category,
        start=start,
        end=end,
        label=label or "<unnamed>",
        content_hash=hashlib.sha256(selected).hexdigest(),
    )


def _remove_targets(root: Path, targets: Sequence[RubyManifestTarget]) -> bool:
    return remove_text_targets(root, targets)


def _remove_target(root: Path, target: RubyManifestTarget) -> bool:
    return _remove_targets(root, (target,))


def _target_location(target: RubyManifestTarget) -> Tuple[Path, int, int]:
    return target.path, target.start, target.end


def _describe_targets(targets: Sequence[RubyManifestTarget]) -> str:
    if len(targets) == 1:
        target = targets[0]
        return "remove Ruby %s from %s" % (target.label, target.path)
    labels = ", ".join(target.label for target in targets[:3])
    if len(targets) > 3:
        labels += ", ..."
    return "remove %d Ruby gem entries: %s" % (len(targets), labels)


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()
