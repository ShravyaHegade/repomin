from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from repomin.batching import try_interval_batches
from repomin.session import ReductionSession
from repomin.text_edits import remove_text_targets


@dataclass(frozen=True)
class PythonSourceTarget:
    path: Path
    category: str
    start: int
    end: int
    label: str
    content_hash: str


class PythonSourceReducer:
    """Reduce Python AST statements while preserving the failure oracle."""

    def __init__(self, session: ReductionSession) -> None:
        self.session = session

    def has_python_sources(self) -> bool:
        return any(
            path.is_file() and not path.is_symlink()
            for path in self.session.current.rglob("*.py")
        )

    def is_applicable(self) -> bool:
        return self.has_python_sources()

    def reduce(self) -> bool:
        with self.session.measure_phase("python-source"):
            return self._reduce()

    def _reduce(self) -> bool:
        accepted_before = self.session.stats.accepted
        while True:
            targets = _discover_targets(self.session.current)
            if not try_interval_batches(
                self.session,
                "python-source",
                targets,
                _target_location,
                _describe_targets,
                remove_text_targets,
            ):
                break
        return self.session.stats.accepted > accepted_before


def _discover_targets(root: Path) -> List[PythonSourceTarget]:
    targets: List[PythonSourceTarget] = []
    for path in sorted(root.rglob("*.py")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = _read_text(path)
            tree = ast.parse(text, filename=str(path), type_comments=True)
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
            continue
        line_starts = _line_starts(text)
        for node in _statement_nodes(tree):
            target = _node_target(
                path.relative_to(root),
                text,
                line_starts,
                node,
            )
            if target is not None:
                targets.append(target)
    return _ordered_unique(targets)


def _statement_nodes(tree: ast.AST) -> Iterable[ast.stmt]:
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt):
            yield node


def _node_target(
    path: Path,
    text: str,
    line_starts: Sequence[int],
    node: ast.stmt,
) -> Optional[PythonSourceTarget]:
    if not hasattr(node, "end_lineno") or not hasattr(node, "end_col_offset"):
        return None
    if node.end_lineno is None or node.end_col_offset is None:
        return None
    start_line = node.lineno
    start_col = node.col_offset
    decorators = getattr(node, "decorator_list", ())
    if decorators:
        start_line = min(start_line, *(decorator.lineno for decorator in decorators))
        start_col = next(
            decorator.col_offset
            for decorator in decorators
            if decorator.lineno == start_line
        )
    start = _position_to_offset(text, line_starts, start_line, start_col)
    end = _position_to_offset(
        text,
        line_starts,
        node.end_lineno,
        node.end_col_offset,
    )
    if start is None or end is None or end <= start:
        return None
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end + 1
    start = line_start
    end = line_end
    if end <= start:
        return None
    category = _node_category(node)
    label = _node_label(node)
    return PythonSourceTarget(
        path=path,
        category=category,
        start=start,
        end=end,
        label=label,
        content_hash=hashlib.sha256(text[start:end].encode("utf-8")).hexdigest(),
    )


def _node_category(node: ast.stmt) -> str:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return "import"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return "definition"
    return "statement"


def _node_label(node: ast.stmt) -> str:
    name = getattr(node, "name", None)
    if isinstance(name, str):
        return name
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        names = getattr(node, "names", ())
        return ", ".join(alias.name for alias in names) or "<unnamed>"
    return type(node).__name__


def _ordered_unique(targets: Iterable[PythonSourceTarget]) -> List[PythonSourceTarget]:
    priorities: Dict[str, int] = {
        "import": 0,
        "definition": 1,
        "statement": 2,
    }
    unique: Dict[Tuple[Path, int, int], PythonSourceTarget] = {}
    for target in targets:
        unique[(target.path, target.start, target.end)] = target
    return sorted(
        unique.values(),
        key=lambda item: (
            priorities.get(item.category, 99),
            -(item.end - item.start),
            item.path.as_posix(),
            item.start,
        ),
    )


def _remove_target(root: Path, target: PythonSourceTarget) -> bool:
    return remove_text_targets(root, (target,))


def _target_location(target: PythonSourceTarget) -> Tuple[Path, int, int]:
    return target.path, target.start, target.end


def _describe_targets(targets: Sequence[PythonSourceTarget]) -> str:
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
    return "remove %d Python source statements: %s" % (len(targets), labels)


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


def _write_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(text)


def _line_starts(text: str) -> List[int]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return starts


def _position_to_offset(
    text: str,
    line_starts: Sequence[int],
    line: int,
    byte_column: int,
) -> Optional[int]:
    if line < 1 or line > len(line_starts) or byte_column < 0:
        return None
    start = line_starts[line - 1]
    newline = text.find("\n", start)
    end = len(text) if newline < 0 else newline
    line_text = text[start:end]
    encoded = line_text.encode("utf-8")
    if byte_column > len(encoded):
        return None
    try:
        prefix = encoded[:byte_column].decode("utf-8")
    except UnicodeDecodeError:
        return None
    return start + len(prefix)
