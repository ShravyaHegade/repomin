from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from repomin.batching import try_hierarchical_batches
from repomin.session import ReductionSession


@dataclass(frozen=True)
class PomTarget:
    pom: Path
    category: str
    key: Tuple[str, ...]
    ordinal: int
    label: str


class MavenReducer:
    """Remove optional Maven model elements while the failure stays reproducible."""

    def __init__(self, session: ReductionSession) -> None:
        self.session = session

    def is_applicable(self) -> bool:
        return any(self.session.current.rglob("pom.xml"))

    def reduce(self) -> None:
        with self.session.measure_phase("maven"):
            self._reduce()

    def _reduce(self) -> None:
        while True:
            targets = list(_discover_targets(self.session.current))
            if not try_hierarchical_batches(
                self.session,
                "maven",
                targets,
                _describe_targets,
                _remove_targets,
            ):
                return


def _discover_targets(root: Path) -> Iterable[PomTarget]:
    for pom_path in sorted(root.rglob("pom.xml")):
        relative = pom_path.relative_to(root)
        try:
            tree = ET.parse(str(pom_path))
        except ET.ParseError:
            continue
        seen: Dict[Tuple[str, Tuple[str, ...]], int] = {}
        for parent in tree.getroot().iter():
            category = _category_for_parent(parent)
            if category is None:
                continue
            for child in list(parent):
                if not _is_candidate(category, child):
                    continue
                key, label = _element_key(category, child)
                seen_key = (category, key)
                ordinal = seen.get(seen_key, 0)
                seen[seen_key] = ordinal + 1
                yield PomTarget(relative, category, key, ordinal, label)


def _remove_target(root: Path, target: PomTarget) -> bool:
    return _remove_targets(root, (target,))


def _remove_targets(root: Path, targets: Sequence[PomTarget]) -> bool:
    by_pom: Dict[Path, List[PomTarget]] = {}
    for target in targets:
        by_pom.setdefault(target.pom, []).append(target)
    if not by_pom:
        return False

    originals: Dict[Path, bytes] = {}
    transformed: Dict[Path, bytes] = {}
    for relative, edits in by_pom.items():
        pom_path = root / relative
        if pom_path.is_symlink():
            return False
        try:
            originals[pom_path] = pom_path.read_bytes()
            tree = ET.parse(str(pom_path))
        except (ET.ParseError, FileNotFoundError, OSError):
            return False
        document = tree.getroot()
        namespace = _namespace(document.tag)
        if namespace:
            ET.register_namespace("", namespace)
        selected = []
        selected_ids = set()
        for target in edits:
            located = _locate_target(document, target)
            if located is None or id(located[1]) in selected_ids:
                return False
            selected.append(located)
            selected_ids.add(id(located[1]))
        for parent, child in selected:
            parent.remove(child)
        if hasattr(ET, "indent"):
            ET.indent(tree, space="  ")
        transformed[pom_path] = ET.tostring(
            document,
            encoding="utf-8",
            xml_declaration=True,
        )

    attempted = []
    try:
        for path, data in transformed.items():
            attempted.append(path)
            path.write_bytes(data)
    except OSError as write_error:
        rollback_failures = []
        for path in reversed(attempted):
            try:
                path.write_bytes(originals[path])
            except OSError:
                rollback_failures.append(path)
        if rollback_failures:
            raise OSError(
                "failed to roll back a partial Maven batch: %s"
                % ", ".join(str(path) for path in rollback_failures)
            ) from write_error
        return False
    return True


def _locate_target(
    document: ET.Element,
    target: PomTarget,
) -> Optional[Tuple[ET.Element, ET.Element]]:
    occurrence = 0
    for parent in document.iter():
        if _category_for_parent(parent) != target.category:
            continue
        for child in list(parent):
            if not _is_candidate(target.category, child):
                continue
            key, _label = _element_key(target.category, child)
            if key != target.key:
                continue
            if occurrence == target.ordinal:
                return parent, child
            occurrence += 1
    return None


def _describe_targets(targets: Sequence[PomTarget]) -> str:
    if len(targets) == 1:
        target = targets[0]
        return "remove Maven %s %s from %s" % (
            target.category,
            target.label,
            target.pom,
        )
    labels = ", ".join(target.label for target in targets[:3])
    if len(targets) > 3:
        labels += ", ..."
    return "remove %d Maven model elements: %s" % (len(targets), labels)


def _category_for_parent(element: ET.Element) -> Optional[str]:
    name = _local_name(element.tag)
    if name == "modules":
        return "module"
    if name == "dependencies":
        return "dependency"
    if name == "plugins":
        return "plugin"
    if name == "properties":
        return "property"
    return None


def _is_candidate(category: str, element: ET.Element) -> bool:
    name = _local_name(element.tag)
    expected = {
        "module": "module",
        "dependency": "dependency",
        "plugin": "plugin",
    }.get(category)
    return expected is None or name == expected


def _element_key(category: str, element: ET.Element) -> Tuple[Tuple[str, ...], str]:
    if category in {"dependency", "plugin"}:
        group = _child_text(element, "groupId")
        artifact = _child_text(element, "artifactId")
        key = (group, artifact)
        label = ":".join(part for part in key if part) or "<unnamed>"
        return key, label
    if category == "module":
        value = (element.text or "").strip()
        return (value,), value or "<empty>"
    name = _local_name(element.tag)
    return (name,), name


def _child_text(element: ET.Element, local_name: str) -> str:
    for child in list(element):
        if _local_name(child.tag) == local_name:
            return (child.text or "").strip()
    return ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return ""
