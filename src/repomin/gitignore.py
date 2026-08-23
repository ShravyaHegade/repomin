from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import List, Sequence, Tuple


class GitignoreError(ValueError):
    """A gitignore-style rule cannot be represented by the supported subset."""


_GLOB_STAR = re.compile(r"\*+")
_GLOB_QUESTION = re.compile(r"\?")


@dataclass(frozen=True)
class GitignoreRule:
    """One compiled gitignore-style rule and its original text.

    The supported subset is deliberately narrower than git's full grammar:

    * blank lines and ``#`` comments are ignored;
    * a leading ``!`` negates an earlier rule;
    * a trailing ``/`` makes a rule directory-only (the directory and every
      descendant match);
    * a leading ``/`` anchors a rule at the rule file's directory;
    * ``*`` does not cross a directory separator while ``**`` does;
    * ``?`` matches one non-separator character;
    * ``[...]`` character classes are supported without escaping.

    Character escaping and rules that contain ``!``, ``#``, or ``[`` only after
    special position are accepted only when the resulting text is unambiguous;
    otherwise the caller should use the explicit ``--ignore-path`` form.
    """

    pattern: str
    regex: "re.Pattern[str]"
    directory_only: bool
    anchored: bool
    negated: bool
    source: str
    line: int
    base: Tuple[str, ...] = ()


def _translate_glob(pattern: str) -> str:
    """Translate a gitignore glob into a non-capturing regex fragment."""
    result: List[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            end = index
            while end < length and pattern[end] == "*":
                end += 1
            count = end - index
            if count >= 2:
                result.append(".*")
            else:
                result.append("[^/]*")
            index = end
            continue
        if char == "?":
            result.append("[^/]")
            index += 1
            continue
        if char == "[":
            end = pattern.find("]", index + 1)
            if end == -1:
                raise GitignoreError("unterminated character class: %s" % pattern)
            body = pattern[index + 1 : end]
            if not body:
                raise GitignoreError("empty character class: %s" % pattern)
            if any(special in body for special in ("/", "\x00")):
                raise GitignoreError(
                    "character classes cannot contain a directory separator: %s"
                    % pattern
                )
            # Git accepts both "!" and "^" as the leading negation marker in
            # a character class. Translate either to regex "^" negation.
            # A leading marker is positional, so a following literal "!" or
            # "^" remains an ordinary class member.
            if body[0] in ("!", "^"):
                body = "^" + body[1:]
            elif body.startswith(r"\!") or body.startswith(r"\^"):
                body = body[1:]
            result.append("[" + body + "]")
            index = end + 1
            continue
        if char == "\\":
            if index + 1 >= length:
                raise GitignoreError("trailing escape in pattern: %s" % pattern)
            result.append(re.escape(pattern[index + 1]))
            index += 2
            continue
        if char in "/.":
            result.append(re.escape(char))
            index += 1
            continue
        result.append(re.escape(char))
        index += 1
    return "".join(result)


def _parse_pattern(
    pattern: str,
    source: str,
    line: int,
    base: Tuple[str, ...] = (),
) -> GitignoreRule:
    negated = pattern.startswith("!")
    if negated:
        pattern = pattern[1:]
    directory_only = pattern.endswith("/")
    if directory_only:
        pattern = pattern.rstrip("/")
    anchored = pattern.startswith("/")
    if anchored:
        pattern = pattern.lstrip("/")
    if not pattern:
        raise GitignoreError(
            "%s:%d: empty pattern" % (source, line)
        )
    if "\x00" in pattern:
        raise GitignoreError(
            "%s:%d: pattern contains NUL" % (source, line)
        )

    contains_separator = "/" in pattern
    # Git anchors any pattern that contains a directory separator at the
    # rule file's directory, even when the leading "/" is omitted. A
    # separator-free pattern instead matches the basename at every depth.
    if contains_separator:
        anchored = True
    if anchored:
        fragment = _translate_glob(pattern)
        if directory_only:
            regex = re.compile(
                r"^%s(?:/.*)?$" % fragment
            )
        else:
            regex = re.compile(r"^%s$" % fragment)
    else:
        fragment = _translate_glob(pattern)
        if directory_only:
            regex = re.compile(
                r"^(?:.*/)?%s(?:/.*)?$" % fragment
            )
        else:
            regex = re.compile(
                r"^(?:.*/)?%s$" % fragment
            )
    return GitignoreRule(
        pattern=pattern,
        regex=regex,
        directory_only=directory_only,
        anchored=anchored,
        negated=negated,
        source=source,
        line=line,
        base=base,
    )


class GitignoreMatcher:
    """Apply an ordered, file-local gitignore-style rule list."""

    def __init__(self, rules: Sequence[GitignoreRule]) -> None:
        self.rules = tuple(rules)

    @classmethod
    def from_text(
        cls,
        text: str,
        source: str = "<gitignore>",
        base: Tuple[str, ...] = (),
    ) -> "GitignoreMatcher":
        rules: List[GitignoreRule] = []
        for line_number, raw in enumerate(text.splitlines(), start=1):
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            rules.append(_parse_pattern(stripped, source, line_number, base))
        return cls(rules)

    @classmethod
    def from_files(
        cls,
        files: Sequence[Tuple[str, str]],
    ) -> "GitignoreMatcher":
        rules: List[GitignoreRule] = []
        for source, text in files:
            rules.extend(cls.from_text(text, source).rules)
        return cls(rules)

    @classmethod
    def from_scoped_files(
        cls,
        files: Sequence[Tuple[str, Tuple[str, ...], str]],
    ) -> "GitignoreMatcher":
        """Parse rules from a sequence of ``(source, base, text)`` entries.

        Entries are processed in call order, which is the repository's
        top-down directory order for nested ``.gitignore`` files.
        """
        rules: List[GitignoreRule] = []
        for source, base, text in files:
            rules.extend(cls.from_text(text, source, base).rules)
        return cls(rules)

    def matches(self, relative: PurePosixPath) -> bool:
        path = relative.as_posix()
        matched = False
        for rule in self.rules:
            if rule.base:
                if relative.parts[: len(rule.base)] != rule.base:
                    continue
                scoped_path = "/".join(relative.parts[len(rule.base) :])
            else:
                scoped_path = path
            if rule.negated:
                if rule.regex.match(scoped_path):
                    matched = False
            elif rule.regex.match(scoped_path):
                matched = True
        return matched

    def serialized_rules(self) -> List[dict]:
        return [
            {
                "pattern": rule.pattern,
                "negated": rule.negated,
                "directory_only": rule.directory_only,
                "anchored": rule.anchored,
                "source": rule.source,
                "line": rule.line,
                "base": list(rule.base),
            }
            for rule in self.rules
        ]
