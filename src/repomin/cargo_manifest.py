from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from repomin.batching import try_interval_batches
from repomin.python_manifest import (
    _array_elements,
    _assignment,
    _lex_toml,
    _read_text,
    _table_header,
    _toml_statements,
    _toml_value_label,
    _whole_statement_range,
)
from repomin.session import ReductionSession
from repomin.text_edits import remove_text_targets


@dataclass(frozen=True)
class CargoManifestTarget:
    path: Path
    category: str
    start: int
    end: int
    label: str
    content_hash: str


class CargoManifestReducer:
    """Reduce Cargo dependency and workspace declarations structurally."""

    def __init__(self, session: ReductionSession) -> None:
        self.session = session

    def is_applicable(self) -> bool:
        return any(
            path.is_file() and not path.is_symlink()
            for path in self.session.current.rglob("Cargo.toml")
        )

    def reduce(self) -> bool:
        with self.session.measure_phase("cargo-manifest"):
            accepted_before = self.session.stats.accepted
            while True:
                targets = _discover_targets(self.session.current)
                if not try_interval_batches(
                    self.session,
                    "cargo-manifest",
                    targets,
                    _target_location,
                    _describe_targets,
                    _remove_targets,
                ):
                    break
            return self.session.stats.accepted > accepted_before


def _discover_targets(root: Path) -> List[CargoManifestTarget]:
    root = root.resolve()
    targets: List[CargoManifestTarget] = []
    for path in sorted(root.rglob("Cargo.toml")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = _read_text(path)
        except (OSError, UnicodeDecodeError):
            continue
        targets.extend(_cargo_targets(path.relative_to(root), text))
    priorities = {
        "dependency": 0,
        "build-dependency": 1,
        "dev-dependency": 2,
        "workspace-member": 3,
        "workspace-exclude": 4,
    }
    return sorted(
        targets,
        key=lambda item: (
            priorities.get(item.category, 99),
            item.path.as_posix(),
            item.start,
        ),
    )


def _cargo_targets(path: Path, text: str) -> Iterable[CargoManifestTarget]:
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
        if _is_dependency_table(table):
            for element, removal_start, removal_end in _array_elements(value):
                yield _target(
                    path,
                    _dependency_category(table),
                    removal_start,
                    removal_end,
                    _toml_value_label(element, text),
                    text,
                )
            if not _is_array(value):
                removal_start, removal_end = _whole_statement_range(text, statement)
                yield _target(
                    path,
                    _dependency_category(table),
                    removal_start,
                    removal_end,
                    key[-1] if key else "<unnamed>",
                    text,
                )
        elif _is_workspace_array(full_key):
            for element, removal_start, removal_end in _array_elements(value):
                category = (
                    "workspace-member"
                    if full_key[-1] == "members"
                    else "workspace-exclude"
                )
                yield _target(
                    path,
                    category,
                    removal_start,
                    removal_end,
                    _toml_value_label(element, text),
                    text,
                )


def _is_dependency_table(table: Tuple[str, ...]) -> bool:
    if not table or table[-1] not in {
        "dependencies",
        "dev-dependencies",
        "build-dependencies",
    }:
        return False
    if len(table) == 1:
        return True
    return table[0] in {"target", "workspace", "patch", "replace"}


def _dependency_category(table: Tuple[str, ...]) -> str:
    return {
        "dependencies": "dependency",
        "dev-dependencies": "dev-dependency",
        "build-dependencies": "build-dependency",
    }[table[-1]]


def _is_workspace_array(full_key: Tuple[str, ...]) -> bool:
    return len(full_key) == 2 and full_key[0] == "workspace" and full_key[1] in {
        "members",
        "exclude",
    }


def _is_array(tokens) -> bool:
    return bool(tokens) and tokens[0].value == "["


def _target(
    path: Path,
    category: str,
    start: int,
    end: int,
    label: str,
    text: str,
) -> CargoManifestTarget:
    selected = text[start:end].encode("utf-8")
    return CargoManifestTarget(
        path=path,
        category=category,
        start=start,
        end=end,
        label=label or "<unnamed>",
        content_hash=hashlib.sha256(selected).hexdigest(),
    )


def _remove_targets(root: Path, targets: Sequence[CargoManifestTarget]) -> bool:
    return remove_text_targets(root, targets)


def _remove_target(root: Path, target: CargoManifestTarget) -> bool:
    return _remove_targets(root, (target,))


def _target_location(target: CargoManifestTarget) -> Tuple[Path, int, int]:
    return target.path, target.start, target.end


def _describe_targets(targets: Sequence[CargoManifestTarget]) -> str:
    if len(targets) == 1:
        target = targets[0]
        return "remove Cargo %s %s from %s" % (
            target.category,
            target.label,
            target.path,
        )
    labels = ", ".join(target.label for target in targets[:3])
    if len(targets) > 3:
        labels += ", ..."
    return "remove %d Cargo manifest entries: %s" % (len(targets), labels)
