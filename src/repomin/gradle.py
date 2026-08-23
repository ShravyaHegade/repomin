from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from repomin.batching import try_interval_batches
from repomin.session import ReductionSession
from repomin.text_edits import remove_text_targets


@dataclass(frozen=True)
class GradleTarget:
    path: Path
    category: str
    start: int
    end: int
    label: str
    content_hash: str


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    start: int
    end: int


class GradleReducer:
    """Reduce Gradle DSL declarations using balanced lexical structure."""

    def __init__(self, session: ReductionSession) -> None:
        self.session = session

    def is_applicable(self) -> bool:
        return any(_gradle_files(self.session.current)) or any(
            self.session.current.rglob("gradle.properties")
        )

    def reduce(self) -> None:
        with self.session.measure_phase("gradle"):
            self._reduce()

    def _reduce(self) -> None:
        while True:
            targets = _discover_targets(self.session.current)
            if not try_interval_batches(
                self.session,
                "gradle",
                targets,
                _target_location,
                _describe_targets,
                remove_text_targets,
            ):
                return


def _discover_targets(root: Path) -> List[GradleTarget]:
    targets: List[GradleTarget] = []
    for path in _gradle_files(root):
        relative = path.relative_to(root)
        try:
            text = _read_text(path)
        except (OSError, UnicodeDecodeError):
            continue
        if path.name in {"settings.gradle", "settings.gradle.kts"}:
            targets.extend(_include_targets(relative, text))
        block_categories = (
            ("dependencies", "dependency"),
            ("plugins", "plugin"),
            ("repositories", "repository"),
            ("configurations", "configuration"),
        )
        for block_name, category in block_categories:
            targets.extend(_block_targets(relative, text, block_name, category))
        targets.extend(
            _empty_block_targets(
                relative,
                text,
                tuple(name for name, _category in block_categories),
            )
        )
    for path in sorted(root.rglob("gradle.properties")):
        if not path.is_file():
            continue
        try:
            text = _read_text(path)
        except (OSError, UnicodeDecodeError):
            continue
        targets.extend(_property_targets(path.relative_to(root), text))
    priorities = {
        "dependency": 0,
        "plugin": 1,
        "repository": 2,
        "configuration": 3,
        "module": 4,
        "property": 5,
        "block": 6,
    }
    return sorted(
        targets,
        key=lambda item: (
            priorities.get(item.category, 99),
            item.path.as_posix(),
            item.start,
        ),
    )


def _gradle_files(root: Path) -> Iterable[Path]:
    paths = set(root.rglob("*.gradle"))
    paths.update(root.rglob("*.gradle.kts"))
    return sorted(path for path in paths if path.is_file())


def _block_targets(
    path: Path,
    text: str,
    block_name: str,
    category: str,
) -> Iterable[GradleTarget]:
    tokens = _lex(text)
    for _name, opening, closing in _named_blocks(tokens, block_name):
        for start_index, end_index in _statements(tokens, opening + 1, closing):
            statement = tokens[start_index:end_index]
            significant = [token for token in statement if token.kind != "newline"]
            if not significant or significant[0].kind != "identifier":
                continue
            start, end = _statement_range(text, significant)
            yield _target(
                path,
                category,
                start,
                end,
                _statement_label(significant),
                text,
            )


def _empty_block_targets(
    path: Path,
    text: str,
    block_names: Sequence[str],
) -> Iterable[GradleTarget]:
    tokens = _lex(text)
    for block_name in block_names:
        for name, opening, closing in _named_blocks(tokens, block_name):
            body = [
                token
                for token in tokens[opening + 1 : closing]
                if token.kind != "newline" and token.value != ";"
            ]
            if body:
                continue
            start, end = _statement_range(text, tokens[name : closing + 1])
            yield _target(path, "block", start, end, block_name, text)


def _include_targets(path: Path, text: str) -> Iterable[GradleTarget]:
    tokens = _lex(text)
    for start_index, end_index in _statements(tokens, 0, len(tokens)):
        statement = tokens[start_index:end_index]
        significant = [token for token in statement if token.kind != "newline"]
        if not significant or significant[0].value != "include":
            continue
        strings = [token for token in significant[1:] if token.kind == "string"]
        for token in strings:
            if len(strings) == 1:
                start, end = _statement_range(text, significant)
            else:
                start, end = _argument_range(significant, token)
            yield _target(
                path,
                "module",
                start,
                end,
                _string_value(token.value),
                text,
            )


def _property_targets(path: Path, text: str) -> Iterable[GradleTarget]:
    offset = 0
    lines = text.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        start = offset
        logical = lines[index]
        offset += len(lines[index])
        index += 1
        while _continues_property(logical) and index < len(lines):
            logical += lines[index]
            offset += len(lines[index])
            index += 1
        stripped = logical.lstrip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        key = _property_key(stripped)
        if not key:
            continue
        yield _target(path, "property", start, offset, key, text)


def _target(
    path: Path,
    category: str,
    start: int,
    end: int,
    label: str,
    text: str,
) -> GradleTarget:
    selected = text[start:end].encode("utf-8")
    return GradleTarget(
        path=path,
        category=category,
        start=start,
        end=end,
        label=label or "<unnamed>",
        content_hash=hashlib.sha256(selected).hexdigest(),
    )


def _remove_target(root: Path, target: GradleTarget) -> bool:
    return remove_text_targets(root, (target,))


def _target_location(target: GradleTarget) -> Tuple[Path, int, int]:
    return target.path, target.start, target.end


def _describe_targets(targets: Sequence[GradleTarget]) -> str:
    if len(targets) == 1:
        target = targets[0]
        return "remove Gradle %s %s from %s" % (
            target.category,
            target.label,
            target.path,
        )
    labels = ", ".join(target.label for target in targets[:3])
    if len(targets) > 3:
        labels += ", ..."
    return "remove %d Gradle model elements: %s" % (len(targets), labels)


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


def _lex(text: str) -> List[_Token]:
    tokens: List[_Token] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in " \t\f\v":
            index += 1
            continue
        if char in "\r\n":
            end = index + 2 if char == "\r" and text[index : index + 2] == "\r\n" else index + 1
            tokens.append(_Token("newline", "\n", index, end))
            index = end
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = len(text) if end < 0 else end + 2
            for position in range(index, end):
                if text[position] == "\n":
                    tokens.append(_Token("newline", "\n", position, position + 1))
            index = end
            continue
        if text.startswith("$/", index):
            end = text.find("/$", index + 2)
            end = len(text) if end < 0 else end + 2
            tokens.append(_Token("string", text[index:end], index, end))
            index = end
            continue
        if char in {"'", '"'}:
            end = _string_end(text, index, char)
            tokens.append(_Token("string", text[index:end], index, end))
            index = end
            continue
        if char.isalpha() or char in {"_", "$"}:
            end = index + 1
            while end < len(text) and (
                text[end].isalnum() or text[end] in {"_", "$", "."}
            ):
                end += 1
            tokens.append(_Token("identifier", text[index:end], index, end))
            index = end
            continue
        if char.isdigit():
            end = index + 1
            while end < len(text) and (text[end].isalnum() or text[end] in {".", "_"}):
                end += 1
            tokens.append(_Token("number", text[index:end], index, end))
            index = end
            continue
        operator = next(
            (
                value
                for value in ("&&", "||", "->", "?:", "?.")
                if text.startswith(value, index)
            ),
            None,
        )
        if operator is not None:
            end = index + len(operator)
            tokens.append(_Token("symbol", operator, index, end))
            index = end
            continue
        tokens.append(_Token("symbol", char, index, index + 1))
        index += 1
    return tokens


def _string_end(text: str, start: int, quote: str) -> int:
    delimiter = quote * 3 if text.startswith(quote * 3, start) else quote
    index = start + len(delimiter)
    while index < len(text):
        if text.startswith(delimiter, index):
            return index + len(delimiter)
        if delimiter == quote and text[index] == "\\":
            index += 2
        else:
            index += 1
    return len(text)


def _named_blocks(
    tokens: Sequence[_Token],
    name: str,
) -> Iterable[Tuple[int, int, int]]:
    for index, token in enumerate(tokens):
        if token.kind != "identifier" or token.value != name:
            continue
        following = _next_significant(tokens, index + 1)
        if following is None or tokens[following].value != "{":
            continue
        closing = _matching_brace(tokens, following)
        if closing is not None:
            yield index, following, closing


def _matching_brace(tokens: Sequence[_Token], opening: int) -> Optional[int]:
    depth = 0
    for index in range(opening, len(tokens)):
        if tokens[index].value == "{":
            depth += 1
        elif tokens[index].value == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _statements(
    tokens: Sequence[_Token],
    start: int,
    end: int,
) -> Iterable[Tuple[int, int]]:
    index = start
    pairs = {"(": ")", "[": "]", "{": "}"}
    while index < end:
        while index < end and (
            tokens[index].kind == "newline" or tokens[index].value == ";"
        ):
            index += 1
        if index >= end:
            return
        statement_start = index
        stack: List[str] = []
        while index < end:
            token = tokens[index]
            if (
                token.kind == "newline"
                and not stack
                and not _continues_statement(tokens, statement_start, index, end)
            ):
                if index > statement_start:
                    yield statement_start, index
                index += 1
                break
            if token.value == ";" and not stack:
                yield statement_start, index + 1
                index += 1
                break
            if token.value in pairs:
                stack.append(pairs[token.value])
            elif stack and token.value == stack[-1]:
                stack.pop()
            index += 1
        else:
            if index > statement_start:
                yield statement_start, index


def _statement_range(text: str, tokens: Sequence[_Token]) -> Tuple[int, int]:
    start = tokens[0].start
    end = tokens[-1].end
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end + 1
    before = text[line_start:start]
    after = text[end:line_end]
    if not before.strip() and (not after.strip() or after.lstrip().startswith("//")):
        return line_start, line_end
    return start, end


def _continues_statement(
    tokens: Sequence[_Token],
    statement_start: int,
    newline: int,
    end: int,
) -> bool:
    previous = _previous_significant(tokens, newline - 1)
    following = _next_significant_before(tokens, newline + 1, end)
    if previous is None or previous < statement_start or following is None:
        return False
    trailing = {",", ".", "=", "+", "-", "*", "/", "&&", "||", "?", "->", "\\"}
    leading = {".", ",", "version", "apply", "because"}
    return tokens[previous].value in trailing or tokens[following].value in leading


def _argument_range(tokens: Sequence[_Token], selected: _Token) -> Tuple[int, int]:
    index = tokens.index(selected)
    following = _next_significant(tokens, index + 1)
    if following is not None and tokens[following].value == ",":
        return selected.start, tokens[following].end
    previous = _previous_significant(tokens, index - 1)
    if previous is not None and tokens[previous].value == ",":
        return tokens[previous].start, selected.end
    return selected.start, selected.end


def _statement_label(tokens: Sequence[_Token]) -> str:
    head = tokens[0].value
    literal = next((token for token in tokens[1:] if token.kind == "string"), None)
    if literal is not None:
        return "%s %s" % (head, _string_value(literal.value))
    preview = "".join(token.value for token in tokens[:5])
    return preview[:120]


def _string_value(value: str) -> str:
    if value.startswith("$/") and value.endswith("/$"):
        return value[2:-2]
    if len(value) >= 6 and value[:3] in {"'''", '\"\"\"'}:
        return value[3:-3]
    if len(value) >= 2 and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _next_significant(tokens: Sequence[_Token], index: int) -> Optional[int]:
    while index < len(tokens):
        if tokens[index].kind != "newline":
            return index
        index += 1
    return None


def _next_significant_before(
    tokens: Sequence[_Token],
    index: int,
    end: int,
) -> Optional[int]:
    while index < end:
        if tokens[index].kind != "newline":
            return index
        index += 1
    return None


def _previous_significant(tokens: Sequence[_Token], index: int) -> Optional[int]:
    while index >= 0:
        if tokens[index].kind != "newline":
            return index
        index -= 1
    return None


def _continues_property(value: str) -> bool:
    line = value.rstrip("\r\n")
    backslashes = 0
    for char in reversed(line):
        if char != "\\":
            break
        backslashes += 1
    return backslashes % 2 == 1


def _property_key(value: str) -> str:
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in {"=", ":"} or char.isspace():
            return value[:index].strip()
    return value.strip()
