from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Tuple

from repomin.batching import try_interval_batches
from repomin.node_manifest import (
    _JsonNode,
    _JsonParser,
    _member_end,
    _member_start,
    _strict_json_loads,
)
from repomin.session import ReductionSession
from repomin.text_edits import remove_text_targets


@dataclass(frozen=True)
class ComposerManifestTarget:
    path: Path
    category: str
    start: int
    end: int
    label: str
    content_hash: str


_OBJECT_CATEGORIES = {
    "require": "dependency",
    "require-dev": "dev-dependency",
    "replace": "replacement",
    "conflict": "conflict",
    "provide": "provided-package",
    "scripts": "script",
}


class ComposerManifestReducer:
    """Reduce safe, structured entries in Composer manifests."""

    def __init__(self, session: ReductionSession) -> None:
        self.session = session

    def is_applicable(self) -> bool:
        return any(
            path.is_file() and not path.is_symlink()
            for path in self.session.current.rglob("composer.json")
        )

    def reduce(self) -> bool:
        with self.session.measure_phase("composer-manifest"):
            accepted_before = self.session.stats.accepted
            while True:
                targets = _discover_targets(self.session.current)
                if not try_interval_batches(
                    self.session,
                    "composer-manifest",
                    targets,
                    _target_location,
                    _describe_targets,
                    _remove_targets,
                ):
                    break
            return self.session.stats.accepted > accepted_before


def _discover_targets(root: Path) -> List[ComposerManifestTarget]:
    root = root.resolve()
    targets: List[ComposerManifestTarget] = []
    for path in sorted(root.rglob("composer.json")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            with path.open("r", encoding="utf-8", newline="") as stream:
                text = stream.read()
            tree = _JsonParser(text).parse()
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if tree.kind != "object":
            continue
        targets.extend(_composer_targets(path.relative_to(root), text, tree))
    priorities = {
        "dependency": 0,
        "dev-dependency": 1,
        "replacement": 2,
        "conflict": 3,
        "provided-package": 4,
        "script": 5,
        "repository": 6,
    }
    return sorted(
        targets,
        key=lambda target: (
            priorities.get(target.category, 99),
            target.path.as_posix(),
            target.start,
        ),
    )


def _composer_targets(
    path: Path,
    text: str,
    tree: _JsonNode,
) -> List[ComposerManifestTarget]:
    targets: List[ComposerManifestTarget] = []
    for member in tree.members:
        category = _OBJECT_CATEGORIES.get(member.key)
        if category is not None and member.value.kind == "object":
            for child in member.value.members:
                targets.append(
                    _target(
                        path,
                        category,
                        _member_start(child),
                        _member_end(child),
                        "%s.%s" % (member.key, child.key),
                        text,
                    )
                )
        if member.key == "repositories" and member.value.kind == "array":
            for index, child in enumerate(member.value.members):
                targets.append(
                    _target(
                        path,
                        "repository",
                        _member_start(child),
                        _member_end(child),
                        _repository_label(text, child.value, index),
                        text,
                    )
                )
    return targets


def _repository_label(text: str, node: _JsonNode, index: int) -> str:
    raw = text[node.start : node.end]
    try:
        value = _strict_json_loads(raw)
    except (TypeError, ValueError):
        return "repositories[%d]" % index
    if isinstance(value, dict) and isinstance(value.get("type"), str):
        return "repositories[%d]=%s" % (index, value["type"])
    if isinstance(value, str):
        return "repositories[%d]=%s" % (index, value)
    return "repositories[%d]" % index


def _target(
    path: Path,
    category: str,
    start: int,
    end: int,
    label: str,
    text: str,
) -> ComposerManifestTarget:
    selected = text[start:end].encode("utf-8")
    return ComposerManifestTarget(
        path=path,
        category=category,
        start=start,
        end=end,
        label=label or "<unnamed>",
        content_hash=hashlib.sha256(selected).hexdigest(),
    )


def _remove_targets(root: Path, targets: Sequence[ComposerManifestTarget]) -> bool:
    return remove_text_targets(root, targets)


def _remove_target(root: Path, target: ComposerManifestTarget) -> bool:
    return _remove_targets(root, (target,))


def _target_location(target: ComposerManifestTarget) -> Tuple[Path, int, int]:
    return target.path, target.start, target.end


def _describe_targets(targets: Sequence[ComposerManifestTarget]) -> str:
    if len(targets) == 1:
        target = targets[0]
        return "remove Composer %s %s from %s" % (
            target.category,
            target.label,
            target.path,
        )
    labels = ", ".join(target.label for target in targets[:3])
    if len(targets) > 3:
        labels += ", ..."
    return "remove %d Composer manifest entries: %s" % (len(targets), labels)
