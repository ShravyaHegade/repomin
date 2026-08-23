from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from repomin.batching import try_interval_batches
from repomin.session import ReductionSession
from repomin.text_edits import remove_text_targets


@dataclass(frozen=True)
class GoManifestTarget:
    path: Path
    category: str
    start: int
    end: int
    label: str
    content_hash: str


_DIRECTIVES = {
    "require": "require",
    "replace": "replace",
    "exclude": "exclude",
    "retract": "retract",
}
_WORK_DIRECTIVES = {
    "use": "use",
    "replace": "replace",
}


class GoManifestReducer:
    """Reduce safe directive entries in Go module and workspace manifests."""

    def __init__(self, session: ReductionSession) -> None:
        self.session = session

    def is_applicable(self) -> bool:
        return any(
            path.is_file() and not path.is_symlink()
            for path in (
                list(self.session.current.rglob("go.mod"))
                + list(self.session.current.rglob("go.work"))
            )
        )

    def reduce(self) -> bool:
        with self.session.measure_phase("go-manifest"):
            accepted_before = self.session.stats.accepted
            while True:
                targets = _discover_targets(self.session.current)
                if not try_interval_batches(
                    self.session,
                    "go-manifest",
                    targets,
                    _target_location,
                    _describe_targets,
                    _remove_targets,
                ):
                    break
            return self.session.stats.accepted > accepted_before


def _discover_targets(root: Path) -> List[GoManifestTarget]:
    root = root.resolve()
    targets: List[GoManifestTarget] = []
    for path in sorted(
        list(root.rglob("go.mod")) + list(root.rglob("go.work"))
    ):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = _read_text(path)
        except (OSError, UnicodeDecodeError):
            continue
        directives = _DIRECTIVES if path.name == "go.mod" else _WORK_DIRECTIVES
        parsed = _go_lines(text, directives)
        if parsed is None:
            continue
        for category, start, end, label in parsed:
            targets.append(
                _target(path.relative_to(root), category, start, end, label, text)
            )
    priorities = {
        "require": 0,
        "use": 1,
        "replace": 2,
        "exclude": 3,
        "retract": 4,
    }
    return sorted(
        targets,
        key=lambda item: (
            priorities.get(item.category, 99),
            item.path.as_posix(),
            item.start,
        ),
    )


def _go_lines(
    text: str,
    directives: dict,
) -> Optional[List[Tuple[str, int, int, str]]]:
    lines = text.splitlines(keepends=True)
    results: List[Tuple[str, int, int, str]] = []
    offset = 0
    block: Optional[str] = None
    for line in lines:
        raw = line.rstrip("\r\n")
        stripped = raw.lstrip()
        start = offset
        end = offset + len(line)
        offset = end
        if not stripped or stripped.startswith("//"):
            continue
        if block is not None:
            if stripped.split("//", 1)[0].strip() == ")":
                block = None
                continue
            if stripped.startswith(")"):
                return None
            if stripped.startswith("//"):
                continue
            category = block
            label = _directive_label(category, stripped)
            if label is not None:
                results.append((category, start, end, label))
            continue
        fields = stripped.split()
        directive = fields[0] if fields else ""
        category = directives.get(directive)
        if category is None:
            continue
        if len(fields) == 2 and fields[1] == "(":
            block = category
            continue
        if "(" in fields[1:]:
            return None
        label = _directive_label(category, stripped)
        if label is not None:
            results.append((category, start, end, label))
    if block is not None:
        return None
    return results


def _directive_label(category: str, line: str) -> Optional[str]:
    without_comment = line.split("//", 1)[0].strip()
    fields = without_comment.split()
    if category == "use":
        if not fields:
            return None
        return fields[1] if fields[0] == "use" and len(fields) > 1 else fields[0]
    if len(fields) < 2:
        return None
    payload = fields[1:] if fields[0] == category else fields
    if not payload:
        return None
    if category in {"require", "use"}:
        return payload[0]
    if category == "replace":
        return " ".join(payload[:4])
    if category == "exclude":
        return " ".join(payload[:2])
    if category == "retract":
        return " ".join(payload)
    return None


def _target(
    path: Path,
    category: str,
    start: int,
    end: int,
    label: str,
    text: str,
) -> GoManifestTarget:
    selected = text[start:end].encode("utf-8")
    return GoManifestTarget(
        path=path,
        category=category,
        start=start,
        end=end,
        label=label or "<unnamed>",
        content_hash=hashlib.sha256(selected).hexdigest(),
    )


def _remove_targets(root: Path, targets: Sequence[GoManifestTarget]) -> bool:
    return remove_text_targets(root, targets)


def _remove_target(root: Path, target: GoManifestTarget) -> bool:
    return _remove_targets(root, (target,))


def _target_location(target: GoManifestTarget) -> Tuple[Path, int, int]:
    return target.path, target.start, target.end


def _describe_targets(targets: Sequence[GoManifestTarget]) -> str:
    if len(targets) == 1:
        target = targets[0]
        return "remove Go %s %s from %s" % (
            target.category,
            target.label,
            target.path,
        )
    labels = ", ".join(target.label for target in targets[:3])
    if len(targets) > 3:
        labels += ", ..."
    return "remove %d Go manifest entries: %s" % (len(targets), labels)


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()
