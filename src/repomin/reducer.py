from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from repomin.session import MutationCandidate, ReductionSession


class FileReducer:
    def __init__(self, session: ReductionSession) -> None:
        self.session = session

    def reduce(self) -> None:
        with self.session.measure_phase("files"):
            self._reduce()

    def _reduce(self) -> None:
        self._reduce_directories()
        keeps = getattr(self.session, "keeps", lambda relative: False)
        while True:
            accepted_before = self.session.stats.accepted
            files = sorted(
                (
                    path.relative_to(self.session.current)
                    for path in self.session.current.rglob("*")
                    if (path.is_file() or path.is_symlink())
                    and not keeps(path.relative_to(self.session.current))
                ),
                key=lambda path: path.as_posix(),
            )
            self._minimize(files, "files")
            if self.session.stats.accepted == accepted_before:
                return
            accepted_before = self.session.stats.accepted
            self._reduce_directories()
            if self.session.stats.accepted == accepted_before:
                return

    def _reduce_directories(self) -> None:
        keeps = getattr(self.session, "keeps", lambda relative: False)
        while True:
            nested_change = False
            depth = 1
            while True:
                directories = sorted(
                    (
                        path.relative_to(self.session.current)
                        for path in self.session.current.rglob("*")
                        if path.is_dir()
                        and not keeps(path.relative_to(self.session.current))
                        and len(path.relative_to(self.session.current).parts) == depth
                    ),
                    key=lambda path: path.as_posix(),
                )
                if not directories:
                    max_depth = max(
                        (
                            len(path.relative_to(self.session.current).parts)
                            for path in self.session.current.rglob("*")
                            if path.is_dir()
                        ),
                        default=0,
                    )
                    if depth > max_depth:
                        break
                else:
                    accepted_before = self.session.stats.accepted
                    self._minimize(directories, "directories")
                    if depth > 1 and self.session.stats.accepted > accepted_before:
                        nested_change = True
                depth += 1
            if not nested_change:
                return

    def _delete_candidate(
        self,
        paths: Sequence[Path],
        kind: str,
    ) -> Optional[Tuple[MutationCandidate, Sequence[Path]]]:
        existing = [
            path
            for path in paths
            if (self.session.current / path).exists()
            or (self.session.current / path).is_symlink()
        ]
        if not existing:
            return None
        stable_paths = tuple(existing)

        def mutation(root: Path) -> bool:
            changed = False
            for relative in stable_paths:
                target = root / relative
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                    changed = True
                elif target.exists() or target.is_symlink():
                    target.unlink()
                    changed = True
            return changed

        description = "remove %d %s: %s" % (
            len(existing),
            kind,
            ", ".join(str(path) for path in existing[:3])
            + ("..." if len(existing) > 3 else ""),
        )
        return MutationCandidate(description, mutation), stable_paths

    def _minimize(
        self,
        items: Sequence[Path],
        kind: str,
    ) -> None:
        remaining: List[Path] = list(items)
        granularity = 1
        while remaining:
            chunks = _partition(remaining, granularity)
            candidates: List[MutationCandidate] = []
            candidate_paths: List[Sequence[Path]] = []
            for chunk in chunks:
                candidate = self._delete_candidate(chunk, kind)
                if candidate is not None:
                    mutation, paths = candidate
                    candidates.append(mutation)
                    candidate_paths.append(paths)
            accepted = self.session.try_mutations("files", candidates)
            if accepted is not None:
                removed = set(candidate_paths[accepted])
                remaining = [item for item in remaining if item not in removed]
                granularity = max(2, granularity - 1)
                continue
            if granularity >= len(remaining):
                break
            granularity = min(len(remaining), granularity * 2)


def _partition(items: Sequence[Path], count: int) -> List[Sequence[Path]]:
    if not items:
        return []
    count = max(1, min(count, len(items)))
    base, extra = divmod(len(items), count)
    chunks: List[Sequence[Path]] = []
    start = 0
    for index in range(count):
        size = base + (1 if index < extra else 0)
        chunks.append(items[start : start + size])
        start += size
    return chunks
