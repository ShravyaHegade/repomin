from __future__ import annotations

import hashlib
import importlib.resources
import json
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from repomin.session import MutationCandidate, ReductionSession


class JavaReducerError(RuntimeError):
    pass


@dataclass(frozen=True)
class JavaAnalysisClasspathEntry:
    path: Path
    kind: str
    fingerprint: str


@dataclass(frozen=True)
class JavaTarget:
    path: Path
    kind: str
    start: int
    end: int
    label: str
    content_hash: str
    replacement: bytes = b"\n"


@dataclass(frozen=True)
class JavaChangeSet:
    kind: str
    label: str
    targets: Tuple[JavaTarget, ...]
    declaration_count: int = 1


JavaCandidate = Union[JavaTarget, JavaChangeSet]


@dataclass(frozen=True)
class _JavaRecord:
    target: JavaTarget
    group: Optional[str] = None
    role: Optional[str] = None


class JavaReducer:
    """Reduce Java declarations and expressions using the JDK compiler AST."""

    def __init__(
        self,
        session: ReductionSession,
        analysis_classpath: Sequence[JavaAnalysisClasspathEntry] = (),
    ) -> None:
        self.session = session
        self.analysis_classpath = tuple(analysis_classpath)

    def has_java_sources(self) -> bool:
        return any(self.session.current.rglob("*.java"))

    def toolchain_available(self) -> bool:
        return shutil.which("java") is not None and shutil.which("javac") is not None

    def is_applicable(self) -> bool:
        return self.has_java_sources() and self.toolchain_available()

    def reduce(self) -> bool:
        with self.session.measure_phase("java"):
            return self._reduce()

    def _reduce(self) -> bool:
        if not self.toolchain_available():
            raise JavaReducerError("the native Java reducer requires JDK 11 or newer")
        accepted_before = self.session.stats.accepted
        with _JavaStructureAnalyzer(self.analysis_classpath) as analyzer:
            while True:
                epoch_accepted = self.session.stats.accepted
                rejected = set()
                while True:
                    targets = analyzer.analyze(self.session.current)
                    keyed = _stable_candidate_keys(targets)
                    selected_targets = [
                        (target, key)
                        for target, key in zip(targets, keyed)
                        if key is None or key not in rejected
                    ]
                    if not selected_targets:
                        break
                    candidates = []
                    keys = []
                    payloads: Dict[int, JavaCandidate] = {}
                    for target, key in selected_targets:
                        description = _describe_candidate(target)

                        def mutation(root: Path, item: JavaCandidate = target) -> bool:
                            return _apply_candidate(root, item)

                        candidate = MutationCandidate(description, mutation)
                        candidates.append(candidate)
                        payloads[id(candidate)] = target
                        keys.append(key)

                    def combine(
                        accepted: Sequence[MutationCandidate],
                    ) -> Optional[MutationCandidate]:
                        return _combine_java_candidates(
                            [payloads[id(candidate)] for candidate in accepted]
                        )

                    accepted_index = self.session.try_mutations(
                        "java",
                        candidates,
                        combine_accepted=combine,
                    )
                    for index, accepted in self.session.last_candidate_decisions.items():
                        key = keys[index]
                        if not accepted and key is not None:
                            rejected.add(key)
                    if accepted_index is None:
                        break
                if self.session.stats.accepted == epoch_accepted:
                    break
        return self.session.stats.accepted > accepted_before


def _describe_candidate(target: JavaCandidate) -> str:
    if isinstance(target, JavaChangeSet):
        call_count = max(0, len(target.targets) - target.declaration_count)
        return (
            "remove Java parameter %s with %d coordinated call-site edit(s)"
            % (target.label, call_count)
        )
    return "remove Java %s %s from %s" % (
        target.kind,
        target.label,
        target.path,
    )


def _stable_candidate_keys(
    candidates: Sequence[JavaCandidate],
) -> List[Optional[Tuple[object, ...]]]:
    bases = []
    counts: Dict[Tuple[object, ...], int] = {}
    for candidate in candidates:
        if isinstance(candidate, JavaChangeSet):
            key = ("change-set", candidate.kind, candidate.label)
        else:
            key = (
                "target",
                candidate.path,
                candidate.kind,
                candidate.label,
                candidate.replacement,
            )
        bases.append(key)
        counts[key] = counts.get(key, 0) + 1
    return [key if counts[key] == 1 else None for key in bases]


def _combine_java_candidates(
    candidates: Sequence[JavaCandidate],
) -> Optional[MutationCandidate]:
    edits = []
    for candidate in candidates:
        candidate_edits = (
            candidate.targets
            if isinstance(candidate, JavaChangeSet)
            else (candidate,)
        )
        for edit in candidate_edits:
            if any(
                existing.path == edit.path
                and existing.end > edit.start
                and edit.end > existing.start
                for existing in edits
            ):
                return None
            edits.append(edit)
    stable_edits = tuple(edits)
    labels = ", ".join(candidate.label for candidate in candidates[:3])
    if len(candidates) > 3:
        labels += ", ..."

    def mutation(root: Path) -> bool:
        return _apply_targets(root, stable_edits)

    return MutationCandidate(
        "apply %d compatible Java reductions: %s" % (len(candidates), labels),
        mutation,
    )


class _JavaStructureAnalyzer:
    def __init__(
        self,
        analysis_classpath: Sequence[JavaAnalysisClasspathEntry] = (),
    ) -> None:
        self.analysis_classpath = tuple(analysis_classpath)
        self._temporary: Optional[tempfile.TemporaryDirectory] = None
        self._classes: Optional[Path] = None

    def __enter__(self) -> "_JavaStructureAnalyzer":
        self._temporary = tempfile.TemporaryDirectory(prefix="repomin-java-")
        root = Path(self._temporary.name)
        source = root / "JavaStructure.java"
        source.write_text(
            importlib.resources.read_text(
                "repomin.java_helper",
                "JavaStructure.java",
                encoding="utf-8",
            ),
            encoding="utf-8",
        )
        self._classes = root / "classes"
        self._classes.mkdir()
        try:
            completed = subprocess.run(
                [
                    _required_executable("javac"),
                    "--release",
                    "11",
                    "-encoding",
                    "UTF-8",
                    "-d",
                    str(self._classes),
                    str(source),
                ],
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            self.close()
            raise JavaReducerError(
                "Java structure helper compilation timed out"
            ) from exc
        if completed.returncode != 0:
            self.close()
            raise JavaReducerError(
                "failed to compile the Java structure helper: %s"
                % (completed.stderr.strip() or completed.stdout.strip())
            )
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
            self._classes = None

    def analyze(self, root: Path) -> List[JavaCandidate]:
        if self._classes is None or self._temporary is None:
            raise JavaReducerError("Java structure analyzer is not initialized")
        _validate_java_analysis_classpath(self.analysis_classpath)
        source_paths = sorted(path for path in root.rglob("*.java") if path.is_file())
        if not source_paths:
            return []
        source_list = Path(self._temporary.name) / "sources.list"
        source_list.write_bytes(
            b"\0".join(os.fsencode(str(path.resolve())) for path in source_paths)
        )
        classpath_list = Path(self._temporary.name) / "classpath.list"
        classpath_list.write_bytes(
            b"\0".join(
                os.fsencode(str(entry.path)) for entry in self.analysis_classpath
            )
        )
        helper_args = [
            _required_executable("java"),
            "-Dfile.encoding=UTF-8",
            "-cp",
            str(self._classes),
            "dev.repomin.internal.JavaStructure",
            "--source-list",
            str(source_list),
            "--classpath-list",
            str(classpath_list),
        ]
        try:
            completed = subprocess.run(
                helper_args,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired as exc:
            raise JavaReducerError("Java structure analysis timed out") from exc
        if completed.returncode != 0:
            raise JavaReducerError(
                "Java structure analysis failed: %s"
                % (completed.stderr.strip() or completed.stdout.strip())
            )
        return _ordered_candidates(_parse_targets(root, completed.stdout))


def prepare_java_analysis_classpath(
    source: Path,
    values: Sequence[str],
) -> Tuple[JavaAnalysisClasspathEntry, ...]:
    try:
        source_root = source.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise JavaReducerError(
            "could not resolve Java analysis source: %s" % source
        ) from exc
    if not source_root.is_dir():
        raise JavaReducerError(
            "Java analysis source is not a directory: %s" % source_root
        )

    paths: List[Path] = []
    for value in values:
        if not value:
            raise JavaReducerError("Java analysis classpath entries must not be empty")
        if "\0" in value:
            raise JavaReducerError(
                "Java analysis classpath entries must not contain NUL"
            )
        try:
            requested = Path(value).expanduser()
            if not requested.is_absolute():
                requested = source_root / requested
            path = requested.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise JavaReducerError(
                "Java analysis classpath entry does not exist or cannot be resolved: %s"
                % value
            ) from exc
        paths.append(path)

    _validate_unique_java_analysis_classpath_paths(paths)
    entries: List[JavaAnalysisClasspathEntry] = []
    for path in paths:
        kind, fingerprint = _fingerprint_java_analysis_classpath_entry(path)
        entries.append(JavaAnalysisClasspathEntry(path, kind, fingerprint))
    _validate_unique_java_analysis_classpath_paths(paths)
    return tuple(entries)


def _validate_java_analysis_classpath(
    entries: Sequence[JavaAnalysisClasspathEntry],
) -> None:
    paths = [entry.path for entry in entries]
    _validate_unique_java_analysis_classpath_paths(paths)
    for entry in entries:
        kind, fingerprint = _fingerprint_java_analysis_classpath_entry(entry.path)
        if kind != entry.kind or fingerprint != entry.fingerprint:
            raise JavaReducerError(
                "Java analysis classpath entry changed after validation: %s"
                % entry.path
            )
    _validate_unique_java_analysis_classpath_paths(paths)


def _validate_unique_java_analysis_classpath_paths(paths: Sequence[Path]) -> None:
    seen_paths = set()
    seen_physical: Dict[Tuple[int, int], Path] = {}
    seen_without_inode: List[Path] = []
    all_seen: List[Path] = []
    for path in paths:
        if path in seen_paths:
            raise JavaReducerError("duplicate Java analysis classpath entry: %s" % path)
        try:
            path_stat = path.lstat()
        except OSError as exc:
            raise JavaReducerError(
                "Java analysis classpath entry is missing or unreadable: %s" % path
            ) from exc

        physical_key = (path_stat.st_dev, path_stat.st_ino)
        try:
            if path_stat.st_ino:
                duplicate = physical_key in seen_physical or any(
                    path.samefile(existing) for existing in seen_without_inode
                )
            else:
                duplicate = any(path.samefile(existing) for existing in all_seen)
        except OSError as exc:
            raise JavaReducerError(
                "Java analysis classpath entry changed while being validated: %s" % path
            ) from exc
        if duplicate:
            raise JavaReducerError("duplicate Java analysis classpath entry: %s" % path)

        seen_paths.add(path)
        all_seen.append(path)
        if path_stat.st_ino:
            seen_physical[physical_key] = path
        else:
            seen_without_inode.append(path)


def _fingerprint_java_analysis_classpath_entry(path: Path) -> Tuple[str, str]:
    digest = hashlib.sha256()
    digest.update(b"repomin-java-analysis-classpath-v1\0")
    try:
        root_stat = path.lstat()
    except OSError as exc:
        raise JavaReducerError(
            "Java analysis classpath entry is missing or unreadable: %s" % path
        ) from exc

    if stat.S_ISLNK(root_stat.st_mode):
        raise JavaReducerError(
            "Java analysis classpath entry must not be a symbolic link: %s" % path
        )
    if stat.S_ISREG(root_stat.st_mode):
        if not os.access(path, os.R_OK):
            raise JavaReducerError(
                "Java analysis classpath file is unreadable: %s" % path
            )
        digest.update(b"file\0")
        _hash_java_classpath_file(path, root_stat, digest)
        return "file", digest.hexdigest()
    if stat.S_ISDIR(root_stat.st_mode):
        if not os.access(path, os.R_OK | os.X_OK):
            raise JavaReducerError(
                "Java analysis classpath directory is unreadable: %s" % path
            )
        digest.update(b"directory\0")
        digest.update(str(stat.S_IMODE(root_stat.st_mode)).encode("ascii"))
        digest.update(b"\0")
        _hash_java_classpath_directory(path, path, digest)
        try:
            root_after = path.lstat()
        except OSError as exc:
            raise JavaReducerError(
                "Java analysis classpath directory changed while being fingerprinted: %s"
                % path
            ) from exc
        if _java_classpath_stat_signature(root_stat) != _java_classpath_stat_signature(
            root_after
        ):
            raise JavaReducerError(
                "Java analysis classpath directory changed while being fingerprinted: %s"
                % path
            )
        return "directory", digest.hexdigest()
    raise JavaReducerError(
        "Java analysis classpath entry must be a regular file or directory: %s" % path
    )


def _hash_java_classpath_directory(
    root: Path,
    directory: Path,
    digest: Any,
) -> None:
    try:
        before = directory.lstat()
    except OSError as exc:
        raise JavaReducerError(
            "Java analysis classpath directory is missing or unreadable: %s" % directory
        ) from exc
    if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise JavaReducerError(
            "Java analysis classpath directory changed while being fingerprinted: %s"
            % directory
        )
    if not os.access(directory, os.R_OK | os.X_OK):
        raise JavaReducerError(
            "Java analysis classpath directory is unreadable: %s" % directory
        )
    try:
        with os.scandir(directory) as stream:
            children = sorted(stream, key=lambda item: os.fsencode(item.name))
    except OSError as exc:
        raise JavaReducerError(
            "Java analysis classpath directory is unreadable: %s" % directory
        ) from exc

    for child in children:
        child_path = Path(child.path)
        relative = child_path.relative_to(root)
        relative_bytes = b"/".join(os.fsencode(part) for part in relative.parts)
        try:
            child_stat = child.stat(follow_symlinks=False)
        except OSError as exc:
            raise JavaReducerError(
                "Java analysis classpath entry is unreadable: %s" % child_path
            ) from exc
        digest.update(relative_bytes)
        digest.update(b"\0")
        digest.update(str(stat.S_IMODE(child_stat.st_mode)).encode("ascii"))
        digest.update(b"\0")
        if stat.S_ISLNK(child_stat.st_mode):
            raise JavaReducerError(
                "Java analysis classpath directories must not contain symbolic links: %s"
                % child_path
            )
        if stat.S_ISDIR(child_stat.st_mode):
            digest.update(b"directory\0")
            _hash_java_classpath_directory(root, child_path, digest)
        elif stat.S_ISREG(child_stat.st_mode):
            digest.update(b"file\0")
            _hash_java_classpath_file(child_path, child_stat, digest)
        else:
            raise JavaReducerError(
                "Java analysis classpath directories must contain only regular files "
                "and directories: %s" % child_path
            )
    try:
        after = directory.lstat()
    except OSError as exc:
        raise JavaReducerError(
            "Java analysis classpath directory changed while being fingerprinted: %s"
            % directory
        ) from exc
    if _java_classpath_stat_signature(before) != _java_classpath_stat_signature(after):
        raise JavaReducerError(
            "Java analysis classpath directory changed while being fingerprinted: %s"
            % directory
        )


def _hash_java_classpath_file(
    path: Path,
    file_stat: os.stat_result,
    digest: Any,
) -> None:
    if not os.access(path, os.R_OK):
        raise JavaReducerError("Java analysis classpath file is unreadable: %s" % path)
    digest.update(str(stat.S_IMODE(file_stat.st_mode)).encode("ascii"))
    digest.update(b"\0")
    try:
        with path.open("rb") as stream:
            opened_before = os.fstat(stream.fileno())
            if _java_classpath_content_signature(
                file_stat
            ) != _java_classpath_content_signature(opened_before):
                raise JavaReducerError(
                    "Java analysis classpath file changed while being fingerprinted: %s"
                    % path
                )
            content_digest = hashlib.sha256()
            content_size = 0
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                content_digest.update(chunk)
                content_size += len(chunk)
            opened_after = os.fstat(stream.fileno())
    except OSError as exc:
        raise JavaReducerError(
            "Java analysis classpath file is unreadable: %s" % path
        ) from exc
    try:
        path_after = path.lstat()
    except OSError as exc:
        raise JavaReducerError(
            "Java analysis classpath file changed while being fingerprinted: %s" % path
        ) from exc
    expected_signature = _java_classpath_stat_signature(file_stat)
    if (
        _java_classpath_content_signature(file_stat)
        != _java_classpath_content_signature(opened_after)
        or expected_signature != _java_classpath_stat_signature(path_after)
        or content_size != file_stat.st_size
    ):
        raise JavaReducerError(
            "Java analysis classpath file changed while being fingerprinted: %s" % path
        )
    digest.update(str(content_size).encode("ascii"))
    digest.update(b"\0")
    digest.update(content_digest.digest())
    digest.update(b"\0")


def _java_classpath_stat_signature(value: os.stat_result) -> Tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_mode,
    )


def _java_classpath_content_signature(value: os.stat_result) -> Tuple[int, ...]:
    """Return metadata stable between Windows paths and open file handles."""
    if os.name == "nt":
        return (value.st_size, value.st_mtime_ns, value.st_mode)
    return _java_classpath_stat_signature(value)


def _parse_targets(root: Path, output: str) -> Iterable[_JavaRecord]:
    resolved_root = root.resolve()
    contents: Dict[Path, bytes] = {}
    for line in output.split("\n"):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            absolute = Path(item["path"]).resolve()
            relative = absolute.relative_to(resolved_root)
            start = int(item["start"])
            end = int(item["end"])
            if absolute not in contents:
                contents[absolute] = absolute.read_bytes()
            data = contents[absolute]
            if start < 0 or end <= start or end > len(data):
                continue
            replacement = b"\n"
            if "replacement_start" in item or "replacement_end" in item:
                replacement_start = int(item["replacement_start"])
                replacement_end = int(item["replacement_end"])
                if (
                    replacement_start < 0
                    or replacement_end <= replacement_start
                    or replacement_end > len(data)
                ):
                    continue
                replacement = data[replacement_start:replacement_end]
            elif "replacement" in item:
                replacement = str(item["replacement"]).encode("utf-8")
            yield _JavaRecord(
                target=JavaTarget(
                    path=relative,
                    kind=str(item["kind"]),
                    start=start,
                    end=end,
                    label=str(item["label"]),
                    content_hash=hashlib.sha256(data[start:end]).hexdigest(),
                    replacement=replacement,
                ),
                group=str(item["group"]) if "group" in item else None,
                role=str(item["role"]) if "role" in item else None,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
            continue


def _ordered_candidates(records: Iterable[_JavaRecord]) -> List[JavaCandidate]:
    targets: List[JavaTarget] = []
    groups: Dict[str, List[_JavaRecord]] = {}
    for record in records:
        if record.group is None:
            targets.append(record.target)
        else:
            groups.setdefault(record.group, []).append(record)

    candidates: List[JavaCandidate] = list(_ordered_unique(targets))
    for records_in_group in groups.values():
        if any(record.role == "blocker" for record in records_in_group):
            continue
        declarations = [
            record for record in records_in_group if record.role == "declaration"
        ]
        call_sites = [record for record in records_in_group if record.role == "call"]
        if not declarations or not call_sites:
            continue
        declaration_targets = [record.target for record in declarations]
        edits = _normalize_targets(declaration_targets + [record.target for record in call_sites])
        if (
            edits is None
            or any(declaration not in edits for declaration in declaration_targets)
            or len(edits) <= len(declaration_targets)
        ):
            continue
        candidates.append(
            JavaChangeSet(
                kind="coordinated-parameter",
                label=declaration_targets[0].label,
                targets=tuple(edits),
                declaration_count=len(declaration_targets),
            )
        )

    priorities: Dict[str, int] = {
        "member": 0,
        "import": 1,
        "statement": 2,
        "annotation": 3,
        "coordinated-parameter": 4,
        "parameter": 5,
        "argument": 6,
        "expression": 7,
        "literal": 8,
    }

    def key(item: JavaCandidate) -> Tuple[object, ...]:
        if isinstance(item, JavaChangeSet):
            first = item.targets[0]
            size = sum(target.end - target.start for target in item.targets)
            return (
                priorities.get(item.kind, 99),
                -size,
                str(first.path),
                first.start,
                item.label,
            )
        return (
            priorities.get(item.kind, 99),
            -(item.end - item.start),
            str(item.path),
            item.start,
            item.replacement,
        )

    return sorted(candidates, key=key)


def _ordered_unique(targets: Iterable[JavaTarget]) -> List[JavaTarget]:
    priorities = {
        "member": 0,
        "import": 1,
        "statement": 2,
        "annotation": 3,
        "parameter": 5,
        "argument": 6,
        "expression": 7,
        "literal": 8,
    }
    return sorted(
        _unique_targets(targets),
        key=lambda item: (
            priorities.get(item.kind, 99),
            -(item.end - item.start),
            str(item.path),
            item.start,
            item.replacement,
        ),
    )


def _unique_targets(targets: Iterable[JavaTarget]) -> List[JavaTarget]:
    unique: Dict[Tuple[Path, int, int, bytes], JavaTarget] = {}
    for target in targets:
        unique[(target.path, target.start, target.end, target.replacement)] = target
    return list(unique.values())


def _normalize_targets(
    targets: Iterable[JavaTarget],
) -> Optional[List[JavaTarget]]:
    unique = _unique_targets(targets)
    discarded = set()
    for left_index, left in enumerate(unique):
        for right_index in range(left_index + 1, len(unique)):
            right = unique[right_index]
            if left.path != right.path:
                continue
            if left.end <= right.start or right.end <= left.start:
                continue
            if left.replacement or right.replacement:
                return None
            if left.start <= right.start and left.end >= right.end:
                discarded.add(right_index)
            elif right.start <= left.start and right.end >= left.end:
                discarded.add(left_index)
            else:
                return None
    return [target for index, target in enumerate(unique) if index not in discarded]


def _apply_candidate(root: Path, candidate: JavaCandidate) -> bool:
    if isinstance(candidate, JavaChangeSet):
        return _apply_targets(root, candidate.targets)
    return _apply_target(root, candidate)


def _apply_target(root: Path, target: JavaTarget) -> bool:
    return _apply_targets(root, (target,))


def _apply_targets(root: Path, targets: Iterable[JavaTarget]) -> bool:
    by_path: Dict[Path, List[JavaTarget]] = {}
    for target in targets:
        by_path.setdefault(target.path, []).append(target)
    if not by_path:
        return False

    originals: Dict[Path, bytes] = {}
    transformed: Dict[Path, bytes] = {}
    for relative, edits in by_path.items():
        path = root / relative
        try:
            data = path.read_bytes()
        except OSError:
            return False
        originals[path] = data
        ordered = sorted(edits, key=lambda item: (item.start, item.end))
        previous_end = -1
        for edit in ordered:
            if edit.start < 0 or edit.end <= edit.start or edit.end > len(data):
                return False
            if edit.start < previous_end:
                return False
            selected = data[edit.start : edit.end]
            if hashlib.sha256(selected).hexdigest() != edit.content_hash:
                return False
            previous_end = edit.end
        for edit in reversed(ordered):
            data = data[: edit.start] + edit.replacement + data[edit.end :]
        transformed[path] = data

    attempted: List[Path] = []
    try:
        for path, data in transformed.items():
            attempted.append(path)
            path.write_bytes(data)
    except OSError as write_error:
        rollback_failures: List[Path] = []
        for path in reversed(attempted):
            try:
                path.write_bytes(originals[path])
            except OSError:
                rollback_failures.append(path)
        if rollback_failures:
            raise JavaReducerError(
                "failed to roll back a partial Java change set: %s"
                % ", ".join(str(path) for path in rollback_failures)
            ) from write_error
        return False
    return True


def _remove_target(root: Path, target: JavaTarget) -> bool:
    """Backward-compatible name for callers that construct removal targets."""
    return _apply_target(root, target)


def _required_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise JavaReducerError("required executable is unavailable: %s" % name)
    return executable
