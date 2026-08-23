from __future__ import annotations

import hashlib
import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from repomin.batching import try_interval_batches
from repomin.session import ReductionSession
from repomin.text_edits import remove_text_targets


@dataclass(frozen=True)
class PythonManifestTarget:
    path: Path
    category: str
    start: int
    end: int
    label: str
    content_hash: str


@dataclass(frozen=True)
class _TomlToken:
    kind: str
    value: str
    start: int
    end: int


class PythonManifestReducer:
    """Reduce Python dependency manifests without rewriting unrelated TOML."""

    def __init__(self, session: ReductionSession) -> None:
        self.session = session

    def is_applicable(self) -> bool:
        return bool(_manifest_files(self.session.current))

    def reduce(self) -> None:
        with self.session.measure_phase("python-manifest"):
            self._reduce()

    def _reduce(self) -> None:
        while True:
            targets = _discover_targets(self.session.current)
            if not try_interval_batches(
                self.session,
                "python-manifest",
                targets,
                _target_location,
                _describe_targets,
                remove_text_targets,
            ):
                return


def _discover_targets(root: Path) -> List[PythonManifestTarget]:
    root = root.resolve()
    targets: List[PythonManifestTarget] = []
    for pyproject in _pyproject_files(root):
        try:
            text = _read_text(pyproject)
        except (OSError, UnicodeDecodeError):
            continue
        targets.extend(_pyproject_targets(pyproject.relative_to(root), text))

    for path in _requirements_files(root):
        try:
            text = _read_text(path)
        except (OSError, UnicodeDecodeError):
            continue
        targets.extend(_requirements_targets(path.relative_to(root), text))

    priorities = {
        "dependency": 0,
        "build dependency": 1,
        "editable dependency": 2,
        "option": 3,
        "constraint": 4,
        "include": 5,
    }
    return sorted(
        targets,
        key=lambda item: (
            priorities.get(item.category, 99),
            item.path.as_posix(),
            item.start,
        ),
    )


def _manifest_files(root: Path) -> List[Path]:
    files: Set[Path] = set(_pyproject_files(root))
    files.update(_requirements_files(root))
    return sorted(files)


def _pyproject_files(root: Path) -> List[Path]:
    return sorted(
        path
        for path in root.rglob("pyproject.toml")
        if path.is_file() and not path.is_symlink()
    )


def _requirements_files(root: Path) -> List[Path]:
    root = root.resolve()
    pending = sorted(
        path
        for path in root.rglob("*.txt")
        if path.is_file()
        and not path.is_symlink()
        and (
            path.name.startswith("requirements")
            or "requirements" in path.relative_to(root).parts[:-1]
        )
    )
    discovered: Set[Path] = set()
    while pending:
        path = pending.pop(0)
        if path in discovered:
            continue
        discovered.add(path)
        try:
            text = _read_text(path)
        except (OSError, UnicodeDecodeError):
            continue
        for _start, _end, logical in _logical_requirement_lines(text):
            _category, include = _classify_requirement(logical)
            if include is None:
                continue
            included = _local_include(root, path.parent, include)
            if included is not None and included not in discovered:
                pending.append(included)
        pending.sort()
    return sorted(discovered)


def _local_include(root: Path, parent: Path, value: str) -> Optional[Path]:
    if not value or "://" in value or value.startswith(("$", "~")):
        return None
    unresolved = parent / value
    if unresolved.is_symlink():
        return None
    candidate = unresolved.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def _pyproject_targets(path: Path, text: str) -> Iterable[PythonManifestTarget]:
    tokens = _lex_toml(text)
    table: Tuple[str, ...] = ()
    for start_index, end_index in _toml_statements(tokens):
        statement = tokens[start_index:end_index]
        header = _table_header(statement)
        if header is not None:
            table = header
            continue
        assignment = _assignment(statement)
        if assignment is None:
            continue
        key, value = assignment
        full_key = table + key
        if _is_dependency_array(full_key):
            category = (
                "build dependency"
                if full_key == ("build-system", "requires")
                else "dependency"
            )
            for element, removal_start, removal_end in _array_elements(value):
                yield _target(
                    path,
                    category,
                    removal_start,
                    removal_end,
                    _toml_value_label(element, text),
                    text,
                )
        elif _is_dependency_table_assignment(full_key):
            removal_start, removal_end = _whole_statement_range(text, statement)
            yield _target(
                path,
                "dependency",
                removal_start,
                removal_end,
                key[-1] if key else "<unnamed>",
                text,
            )


def _is_dependency_array(full_key: Tuple[str, ...]) -> bool:
    if full_key == ("project", "dependencies"):
        return True
    if len(full_key) == 3 and full_key[:2] == (
        "project",
        "optional-dependencies",
    ):
        return True
    if full_key == ("build-system", "requires"):
        return True
    if len(full_key) == 2 and full_key[0] == "dependency-groups":
        return True
    if len(full_key) == 4 and full_key[:3] == (
        "tool",
        "pdm",
        "dev-dependencies",
    ):
        return True
    return full_key == ("tool", "uv", "dev-dependencies")


def _is_dependency_table_assignment(full_key: Tuple[str, ...]) -> bool:
    parent = full_key[:-1]
    if parent in {
        ("tool", "poetry", "dependencies"),
        ("tool", "poetry", "dev-dependencies"),
        ("tool", "pdm", "dependencies"),
    }:
        return True
    return (
        len(parent) >= 5
        and parent[:3] == ("tool", "poetry", "group")
        and parent[-1] == "dependencies"
    )


def _lex_toml(text: str) -> List[_TomlToken]:
    tokens: List[_TomlToken] = []
    index = 0
    symbols = "[]= {},."
    while index < len(text):
        char = text[index]
        if char in " \t\f\v":
            index += 1
            continue
        if char in "\r\n":
            end = (
                index + 2
                if char == "\r" and text[index : index + 2] == "\r\n"
                else index + 1
            )
            tokens.append(_TomlToken("newline", "\n", index, end))
            index = end
            continue
        if char == "#":
            newline = text.find("\n", index + 1)
            index = len(text) if newline < 0 else newline
            continue
        if char in {"'", '"'}:
            end = _toml_string_end(text, index, char)
            tokens.append(_TomlToken("string", text[index:end], index, end))
            index = end
            continue
        if char in symbols:
            if char != " ":
                tokens.append(_TomlToken("symbol", char, index, index + 1))
            index += 1
            continue
        end = index + 1
        while end < len(text):
            following = text[end]
            if following.isspace() or following in symbols or following in {"#", "'", '"'}:
                break
            end += 1
        tokens.append(_TomlToken("bare", text[index:end], index, end))
        index = end
    return tokens


def _toml_string_end(text: str, start: int, quote: str) -> int:
    delimiter = quote * 3 if text.startswith(quote * 3, start) else quote
    index = start + len(delimiter)
    while index < len(text):
        if text.startswith(delimiter, index):
            return index + len(delimiter)
        if quote == '"' and text[index] == "\\":
            if len(delimiter) == 3 and index + 1 < len(text) and text[index + 1] in "\r\n":
                index += 2
                if text[index - 1] == "\r" and index < len(text) and text[index] == "\n":
                    index += 1
                while index < len(text) and text[index] in " \t\r\n":
                    index += 1
            else:
                index += 2
        else:
            index += 1
    return len(text)


def _toml_statements(tokens: Sequence[_TomlToken]) -> Iterable[Tuple[int, int]]:
    index = 0
    pairs = {"[": "]", "{": "}"}
    while index < len(tokens):
        while index < len(tokens) and tokens[index].kind == "newline":
            index += 1
        if index >= len(tokens):
            return
        start = index
        stack: List[str] = []
        while index < len(tokens):
            token = tokens[index]
            if token.kind == "newline" and not stack:
                if index > start:
                    yield start, index
                index += 1
                break
            if token.value in pairs:
                stack.append(pairs[token.value])
            elif stack and token.value == stack[-1]:
                stack.pop()
            index += 1
        else:
            if index > start:
                yield start, index


def _table_header(tokens: Sequence[_TomlToken]) -> Optional[Tuple[str, ...]]:
    if len(tokens) < 3 or tokens[0].value != "[" or tokens[-1].value != "]":
        return None
    if tokens[1].value == "[" or tokens[-2].value == "]":
        return None
    return _key(tokens[1:-1])


def _assignment(
    tokens: Sequence[_TomlToken],
) -> Optional[Tuple[Tuple[str, ...], Sequence[_TomlToken]]]:
    stack: List[str] = []
    pairs = {"[": "]", "{": "}"}
    for index, token in enumerate(tokens):
        if token.value == "=" and not stack:
            key = _key(tokens[:index])
            if key is not None:
                return key, tokens[index + 1 :]
            return None
        if token.value in pairs:
            stack.append(pairs[token.value])
        elif stack and token.value == stack[-1]:
            stack.pop()
    return None


def _key(tokens: Sequence[_TomlToken]) -> Optional[Tuple[str, ...]]:
    if not tokens:
        return None
    parts: List[str] = []
    expect_value = True
    for token in tokens:
        if expect_value:
            if token.kind not in {"bare", "string"}:
                return None
            parts.append(_toml_key_value(token))
        elif token.value != ".":
            return None
        expect_value = not expect_value
    if expect_value:
        return None
    return tuple(parts)


def _toml_key_value(token: _TomlToken) -> str:
    if token.kind != "string":
        return token.value
    value = token.value
    if value.startswith("'''") and value.endswith("'''"):
        return value[3:-3]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    if value.startswith('"""') and value.endswith('"""'):
        value = '"' + value[3:-3] + '"'
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return value.strip('"')
    return decoded if isinstance(decoded, str) else str(decoded)


def _array_elements(
    tokens: Sequence[_TomlToken],
) -> Iterable[Tuple[Sequence[_TomlToken], int, int]]:
    if len(tokens) < 2 or tokens[0].value != "[":
        return
    segments: List[Tuple[int, int, Optional[int], Optional[int]]] = []
    stack = ["]"]
    segment_start = 1
    previous_comma: Optional[int] = None
    closing: Optional[int] = None
    pairs = {"[": "]", "{": "}"}
    for index in range(1, len(tokens)):
        token = tokens[index]
        if token.value == "," and len(stack) == 1:
            segments.append((segment_start, index, previous_comma, index))
            previous_comma = index
            segment_start = index + 1
            continue
        if token.value in pairs:
            stack.append(pairs[token.value])
            continue
        if stack and token.value == stack[-1]:
            stack.pop()
            if not stack:
                closing = index
                break
    if closing is None:
        return
    if segment_start < closing:
        segments.append((segment_start, closing, previous_comma, None))
    for segment_start, segment_end, previous, following in segments:
        element = [
            token
            for token in tokens[segment_start:segment_end]
            if token.kind != "newline"
        ]
        if not element:
            continue
        if following is not None:
            removal_start = element[0].start
            removal_end = tokens[following].end
        elif previous is not None:
            removal_start = tokens[previous].start
            removal_end = element[-1].end
        else:
            removal_start = element[0].start
            removal_end = element[-1].end
        yield element, removal_start, removal_end


def _whole_statement_range(
    text: str,
    tokens: Sequence[_TomlToken],
) -> Tuple[int, int]:
    start = text.rfind("\n", 0, tokens[0].start) + 1
    newline = text.find("\n", tokens[-1].end)
    end = len(text) if newline < 0 else newline + 1
    return start, end


def _toml_value_label(tokens: Sequence[_TomlToken], text: str) -> str:
    if len(tokens) == 1 and tokens[0].kind == "string":
        return _toml_key_value(tokens[0]) or "<empty>"
    start = tokens[0].start
    end = tokens[-1].end
    return " ".join(text[start:end].split())[:120] or "<empty>"


def _requirements_targets(
    path: Path,
    text: str,
) -> Iterable[PythonManifestTarget]:
    for start, end, logical in _logical_requirement_lines(text):
        category, _include = _classify_requirement(logical)
        if category is None:
            continue
        label = " ".join(logical.replace("\\\r\n", " ").replace("\\\n", " ").split())
        yield _target(path, category, start, end, label[:120], text)


def _logical_requirement_lines(text: str) -> Iterable[Tuple[int, int, str]]:
    lines = text.splitlines(keepends=True)
    offset = 0
    index = 0
    while index < len(lines):
        start = offset
        logical = lines[index]
        offset += len(lines[index])
        index += 1
        while _continues_requirement(logical) and index < len(lines):
            logical += lines[index]
            offset += len(lines[index])
            index += 1
        yield start, offset, logical


def _continues_requirement(value: str) -> bool:
    line = value.rstrip("\r\n")
    backslashes = 0
    for char in reversed(line):
        if char != "\\":
            break
        backslashes += 1
    return backslashes % 2 == 1


def _classify_requirement(value: str) -> Tuple[Optional[str], Optional[str]]:
    stripped = value.lstrip()
    if not stripped or stripped.startswith("#"):
        return None, None
    logical = value.replace("\\\r\n", " ").replace("\\\n", " ")
    try:
        words = shlex.split(logical, comments=False, posix=True)
    except ValueError:
        words = logical.split()
    if not words:
        return None, None
    first = words[0]
    include = _option_argument(first, words, "-r", "--requirement")
    if include is not None:
        return "include", include
    constraint = _option_argument(first, words, "-c", "--constraint")
    if constraint is not None:
        return "constraint", constraint
    if first in {"-e", "--editable"} or first.startswith("--editable="):
        return "editable dependency", None
    if first.startswith("-"):
        return "option", None
    return "dependency", None


def _option_argument(
    first: str,
    words: Sequence[str],
    short: str,
    long: str,
) -> Optional[str]:
    if first in {short, long}:
        return words[1] if len(words) > 1 else ""
    if first.startswith(long + "="):
        return first[len(long) + 1 :]
    if first.startswith(short) and first != short:
        return first[len(short) :]
    return None


def _target(
    path: Path,
    category: str,
    start: int,
    end: int,
    label: str,
    text: str,
) -> PythonManifestTarget:
    selected = text[start:end].encode("utf-8")
    return PythonManifestTarget(
        path=path,
        category=category,
        start=start,
        end=end,
        label=label or "<unnamed>",
        content_hash=hashlib.sha256(selected).hexdigest(),
    )


def _remove_target(root: Path, target: PythonManifestTarget) -> bool:
    return remove_text_targets(root, (target,))


def _target_location(target: PythonManifestTarget) -> Tuple[Path, int, int]:
    return target.path, target.start, target.end


def _describe_targets(targets: Sequence[PythonManifestTarget]) -> str:
    if len(targets) == 1:
        target = targets[0]
        return "remove Python %s %s from %s" % (
            target.category,
            target.label,
            target.path,
        )
    labels = ", ".join(target.label for target in targets[:3])
    if len(targets) > 3:
        labels += ", ..."
    return "remove %d Python manifest entries: %s" % (len(targets), labels)


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()
