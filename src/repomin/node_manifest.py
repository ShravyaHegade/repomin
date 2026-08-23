from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from repomin.batching import try_interval_batches
from repomin.session import ReductionSession
from repomin.text_edits import remove_text_targets


@dataclass(frozen=True)
class NodeManifestTarget:
    path: Path
    category: str
    start: int
    end: int
    label: str
    content_hash: str


@dataclass(frozen=True)
class _JsonMember:
    key: str
    start: int
    value_end: int
    comma_before: Optional[int]
    comma_after: Optional[int]
    value: "_JsonNode"


@dataclass(frozen=True)
class _JsonNode:
    kind: str
    start: int
    end: int
    members: Tuple[_JsonMember, ...] = ()
    items: Tuple["_JsonNode", ...] = ()


class _JsonParser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.index = 0

    def parse(self) -> _JsonNode:
        value = self._value()
        self._space()
        if self.index != len(self.text):
            raise ValueError("trailing JSON content")
        return value

    def _value(self) -> _JsonNode:
        self._space()
        start = self.index
        if start >= len(self.text):
            raise ValueError("missing JSON value")
        char = self.text[start]
        if char == "{":
            return self._object()
        if char == "[":
            return self._array()
        if char == '"':
            self._string()
            _strict_json_loads(self.text[start : self.index])
            return _JsonNode("scalar", start, self.index)
        for literal in ("true", "false", "null"):
            if self.text.startswith(literal, start):
                self.index += len(literal)
                return _JsonNode("scalar", start, self.index)
        self._number()
        return _JsonNode("scalar", start, self.index)

    def _object(self) -> _JsonNode:
        start = self.index
        self.index += 1
        members: List[_JsonMember] = []
        seen = set()
        comma_before: Optional[int] = None
        self._space()
        if self._take("}"):
            return _JsonNode("object", start, self.index, tuple(members))
        while True:
            self._space()
            member_start = self.index
            key = self._string_value()
            if key in seen:
                raise ValueError("duplicate JSON object key")
            seen.add(key)
            self._space()
            if not self._take(":"):
                raise ValueError("missing JSON object colon")
            value = self._value()
            self._space()
            comma_after: Optional[int] = None
            if self._take(","):
                comma_after = self.index - 1
            elif self.index >= len(self.text) or self.text[self.index] != "}":
                raise ValueError("missing JSON object delimiter")
            members.append(
                _JsonMember(
                    key,
                    member_start,
                    value.end,
                    comma_before,
                    comma_after,
                    value,
                )
            )
            if comma_after is None:
                self.index += 1
                break
            comma_before = comma_after
            self._space()
            if self.index < len(self.text) and self.text[self.index] == "}":
                raise ValueError("trailing JSON comma")
        return _JsonNode("object", start, self.index, tuple(members))

    def _array(self) -> _JsonNode:
        start = self.index
        self.index += 1
        items: List[_JsonNode] = []
        comma_before: Optional[int] = None
        item_ranges: List[Tuple[_JsonNode, Optional[int], Optional[int]]] = []
        self._space()
        if self._take("]"):
            return _JsonNode("array", start, self.index, items=tuple(items))
        while True:
            item = self._value()
            self._space()
            comma_after: Optional[int] = None
            if self._take(","):
                comma_after = self.index - 1
            elif self.index >= len(self.text) or self.text[self.index] != "]":
                raise ValueError("missing JSON array delimiter")
            item_ranges.append((item, comma_before, comma_after))
            items.append(item)
            if comma_after is None:
                self.index += 1
                break
            comma_before = comma_after
            self._space()
            if self.index < len(self.text) and self.text[self.index] == "]":
                raise ValueError("trailing JSON comma")
        # Arrays reuse _JsonMember so range handling stays identical to objects.
        members = tuple(
            _JsonMember("", item.start, item.end, before, after, item)
            for item, before, after in item_ranges
        )
        return _JsonNode("array", start, self.index, members=members, items=tuple(items))

    def _string_value(self) -> str:
        start = self.index
        self._string()
        value = self.text[start : self.index]
        decoded = _strict_json_loads(value)
        if not isinstance(decoded, str):
            raise ValueError("JSON object key is not a string")
        return decoded

    def _string(self) -> None:
        if not self._take('"'):
            raise ValueError("expected JSON string")
        escaped = False
        while self.index < len(self.text):
            char = self.text[self.index]
            self.index += 1
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
            elif char == '"':
                return
            elif ord(char) < 0x20:
                raise ValueError("control character in JSON string")
        raise ValueError("unterminated JSON string")

    def _number(self) -> None:
        start = self.index
        while self.index < len(self.text) and self.text[self.index] not in ",]} \t\r\n":
            self.index += 1
        token = self.text[start : self.index]
        if not token:
            raise ValueError("missing JSON literal")
        try:
            value = _strict_json_loads(token)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid JSON literal") from exc
        if isinstance(value, (dict, list, str)) or isinstance(value, bool) or value is None:
            raise ValueError("invalid JSON number")

    def _space(self) -> None:
        while self.index < len(self.text) and self.text[self.index] in " \t\r\n":
            self.index += 1

    def _take(self, value: str) -> bool:
        if self.text.startswith(value, self.index):
            self.index += len(value)
            return True
        return False


_OBJECT_CATEGORIES = {
    "dependencies": "dependency",
    "devDependencies": "dev-dependency",
    "optionalDependencies": "optional-dependency",
    "peerDependencies": "peer-dependency",
    "scripts": "script",
    "resolutions": "resolution",
    "overrides": "override",
}
_ARRAY_CATEGORIES = {
    "workspaces": "workspace",
    "files": "file",
    "bundledDependencies": "bundled-dependency",
    "bundleDependencies": "bundled-dependency",
}


class NodeManifestReducer:
    """Reduce safe, structured entries in npm-compatible package manifests."""

    def __init__(self, session: ReductionSession) -> None:
        self.session = session

    def is_applicable(self) -> bool:
        return any(
            path.is_file() and not path.is_symlink()
            for path in self.session.current.rglob("package.json")
        )

    def reduce(self) -> bool:
        with self.session.measure_phase("node-manifest"):
            accepted_before = self.session.stats.accepted
            while True:
                targets = _discover_targets(self.session.current)
                if not try_interval_batches(
                    self.session,
                    "node-manifest",
                    targets,
                    _target_location,
                    _describe_targets,
                    _remove_targets,
                ):
                    break
            return self.session.stats.accepted > accepted_before


def _discover_targets(root: Path) -> List[NodeManifestTarget]:
    targets: List[NodeManifestTarget] = []
    for path in sorted(root.rglob("package.json")):
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
        targets.extend(_node_targets(path.relative_to(root), text, tree))
    return sorted(
        targets,
        key=lambda target: (target.category, target.path.as_posix(), target.start),
    )


def _node_targets(
    path: Path,
    text: str,
    node: _JsonNode,
    prefix: Tuple[str, ...] = (),
) -> List[NodeManifestTarget]:
    targets: List[NodeManifestTarget] = []
    if node.kind == "object":
        for member in node.members:
            category = _OBJECT_CATEGORIES.get(member.key)
            if category is not None and member.value.kind == "object":
                for child in member.value.members:
                    targets.append(
                        _target(
                            path,
                            category,
                            _member_start(child),
                            _member_end(child),
                            ".".join(prefix + (member.key, child.key)),
                            text,
                        )
                    )
            targets.extend(
                _node_targets(path, text, member.value, prefix + (member.key,))
            )
    elif node.kind == "array":
        key = prefix[-1] if prefix else ""
        category = _ARRAY_CATEGORIES.get(key)
        if category is not None:
            for index, member in enumerate(node.members):
                if member.value.kind != "scalar":
                    continue
                value = text[member.value.start : member.value.end]
                try:
                    decoded = _strict_json_loads(value)
                except (TypeError, ValueError):
                    continue
                if not isinstance(decoded, str):
                    continue
                targets.append(
                    _target(
                        path,
                        category,
                        _member_start(member),
                        _member_end(member),
                        ".".join(prefix) + "[%d]=%s" % (index, decoded),
                        text,
                    )
                )
        for member in node.members:
            targets.extend(_node_targets(path, text, member.value, prefix))
    return targets


def _member_start(member: _JsonMember) -> int:
    return member.comma_before if member.comma_after is None and member.comma_before is not None else member.start


def _member_end(member: _JsonMember) -> int:
    return (member.comma_after + 1) if member.comma_after is not None else member.value_end


def _target(
    path: Path,
    category: str,
    start: int,
    end: int,
    label: str,
    text: str,
) -> NodeManifestTarget:
    selected = text[start:end].encode("utf-8")
    return NodeManifestTarget(
        path=path,
        category=category,
        start=start,
        end=end,
        label=label or "<unnamed>",
        content_hash=hashlib.sha256(selected).hexdigest(),
    )


def _strict_json_loads(value: str):
    def reject_constant(constant: str):
        raise ValueError("non-standard JSON constant: %s" % constant)

    return json.loads(value, parse_constant=reject_constant)


def _remove_targets(root: Path, targets: Sequence[NodeManifestTarget]) -> bool:
    return remove_text_targets(root, targets)


def _remove_target(root: Path, target: NodeManifestTarget) -> bool:
    return _remove_targets(root, (target,))


def _target_location(target: NodeManifestTarget) -> Tuple[Path, int, int]:
    return target.path, target.start, target.end


def _describe_targets(targets: Sequence[NodeManifestTarget]) -> str:
    if len(targets) == 1:
        target = targets[0]
        return "remove Node %s %s from %s" % (
            target.category,
            target.label,
            target.path,
        )
    labels = ", ".join(target.label for target in targets[:3])
    if len(targets) > 3:
        labels += ", ..."
    return "remove %d Node package entries: %s" % (len(targets), labels)
