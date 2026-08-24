from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from repomin.batching import try_interval_batches
from repomin.python_manifest import (
    _assignment,
    _lex_toml,
    _read_text,
    _table_header,
    _toml_statements,
    _whole_statement_range,
)
from repomin.session import ReductionSession
from repomin.text_edits import remove_text_targets


@dataclass(frozen=True)
class PipenvManifestTarget:
    path: Path
    category: str
    start: int
    end: int
    label: str
    content_hash: str


class PipenvManifestReducer:
    """Reduce direct Pipenv declarations in ``Pipfile`` manifests."""

    def __init__(self, session: ReductionSession) -> None:
        self.session = session

    def is_applicable(self) -> bool:
        return any(_pipfile_files(self.session.current))

    def reduce(self) -> bool:
        with self.session.measure_phase("pipenv-manifest"):
            accepted_before = self.session.stats.accepted
            while True:
                targets = _discover_targets(self.session.current)
                if not try_interval_batches(
                    self.session,
                    "pipenv-manifest",
                    targets,
                    _target_location,
                    _describe_targets,
                    _remove_targets,
                ):
                    break
            return self.session.stats.accepted > accepted_before


def _pipfile_files(root: Path) -> List[Path]:
    return sorted(
        path
        for path in root.resolve().rglob("Pipfile")
        if path.is_file() and not path.is_symlink()
    )


def _discover_targets(root: Path) -> List[PipenvManifestTarget]:
    root = root.resolve()
    targets: List[PipenvManifestTarget] = []
    for path in _pipfile_files(root):
        try:
            text = _read_text(path)
        except (OSError, UnicodeDecodeError):
            continue
        targets.extend(_pipfile_targets(path.relative_to(root), text))
    priorities = {"dependency": 0, "dev-dependency": 1, "option": 2}
    return sorted(
        targets,
        key=lambda item: (
            priorities.get(item.category, 99),
            item.path.as_posix(),
            item.start,
        ),
    )


def _pipfile_targets(
    path: Path, text: str
) -> Iterable[PipenvManifestTarget]:
    tokens = _lex_toml(text)
    table: Tuple[str, ...] = ()
    categories = {
        ("packages",): "dependency",
        ("dev-packages",): "dev-dependency",
        ("requires",): "option",
    }
    for start_index, end_index in _toml_statements(tokens):
        statement = tokens[start_index:end_index]
        header = _table_header(statement)
        if header is not None:
            table = header
            continue
        category = categories.get(table)
        if category is None:
            continue
        assignment = _assignment(statement)
        if assignment is None:
            continue
        key, _value = assignment
        start, end = _whole_statement_range(text, statement)
        yield _target(path, category, start, end, key[-1] if key else "<unnamed>", text)


def _target(
    path: Path,
    category: str,
    start: int,
    end: int,
    label: str,
    text: str,
) -> PipenvManifestTarget:
    selected = text[start:end].encode("utf-8")
    return PipenvManifestTarget(
        path=path,
        category=category,
        start=start,
        end=end,
        label=label or "<unnamed>",
        content_hash=hashlib.sha256(selected).hexdigest(),
    )


def _remove_targets(root: Path, targets: Sequence[PipenvManifestTarget]) -> bool:
    return remove_text_targets(root, targets)


def _remove_target(root: Path, target: PipenvManifestTarget) -> bool:
    return _remove_targets(root, (target,))


def _target_location(target: PipenvManifestTarget) -> Tuple[Path, int, int]:
    return target.path, target.start, target.end


def _describe_targets(targets: Sequence[PipenvManifestTarget]) -> str:
    if len(targets) == 1:
        target = targets[0]
        return "remove Pipenv %s %s from %s" % (
            target.category,
            target.label,
            target.path,
        )
    labels = ", ".join(target.label for target in targets[:3])
    if len(targets) > 3:
        labels += ", ..."
    return "remove %d Pipenv manifest entries: %s" % (len(targets), labels)
