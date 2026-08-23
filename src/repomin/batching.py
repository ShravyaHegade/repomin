from __future__ import annotations

import heapq
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple, TypeVar

from repomin.session import MutationCandidate, ReductionSession

T = TypeVar("T")


def try_hierarchical_batches(
    session: ReductionSession,
    phase: str,
    items: Sequence[T],
    describe: Callable[[Sequence[T]], str],
    mutate: Callable[[Path, Sequence[T]], bool],
) -> bool:
    """Try progressively smaller deterministic batches until one is accepted."""
    stable = tuple(items)
    if not stable:
        return False
    granularity = 1
    while True:
        chunks = _partition(stable, granularity)
        candidates = []
        for chunk in chunks:

            def mutation(root: Path, selected: Sequence[T] = chunk) -> bool:
                return mutate(root, selected)

            candidates.append(MutationCandidate(describe(chunk), mutation))
        if session.try_mutations(phase, candidates) is not None:
            return True
        if granularity >= len(stable):
            return False
        granularity = min(len(stable), granularity * 2)


def interval_layers(
    items: Sequence[T],
    location: Callable[[T], Tuple[Path, int, int]],
) -> List[List[List[T]]]:
    """Return containment layers split into non-overlapping deterministic packs.

    Every item appears exactly once. A containing interval is tested before its
    descendants; partially overlapping intervals are placed in separate packs.
    """
    stable: List[T] = []
    seen = set()
    locations: List[Tuple[Path, int, int]] = []
    for item in items:
        item_location = location(item)
        if item_location in seen:
            continue
        seen.add(item_location)
        stable.append(item)
        locations.append(item_location)

    depths = _containment_depths(locations)

    by_depth: Dict[int, List[T]] = {}
    for index, item in enumerate(stable):
        by_depth.setdefault(depths[index], []).append(item)
    return [
        _compatible_packs(by_depth[level], location)
        for level in sorted(by_depth)
    ]


def try_interval_batches(
    session: ReductionSession,
    phase: str,
    items: Sequence[T],
    location: Callable[[T], Tuple[Path, int, int]],
    describe: Callable[[Sequence[T]], str],
    mutate: Callable[[Path, Sequence[T]], bool],
) -> bool:
    """Try every containment frontier and compatible pack for interval edits."""
    for layer in interval_layers(items, location):
        for pack in layer:
            if try_hierarchical_batches(
                session,
                phase,
                pack,
                describe,
                mutate,
            ):
                return True
    return False


def _compatible_packs(
    items: Sequence[T],
    location: Callable[[T], Tuple[Path, int, int]],
) -> List[List[T]]:
    locations = [location(item) for item in items]
    by_path: Dict[Path, List[int]] = {}
    for index, (path, _start, _end) in enumerate(locations):
        by_path.setdefault(path, []).append(index)

    colors: Dict[int, int] = {}
    color_count = 0
    for indices in by_path.values():
        active: List[Tuple[int, int]] = []
        available: List[int] = []
        next_color = 0
        for index in sorted(
            indices,
            key=lambda value: (locations[value][1], locations[value][2], value),
        ):
            _path, start, end = locations[index]
            while active and active[0][0] <= start:
                _finished, color = heapq.heappop(active)
                heapq.heappush(available, color)
            if available:
                color = heapq.heappop(available)
            else:
                color = next_color
                next_color += 1
            colors[index] = color
            heapq.heappush(active, (end, color))
        color_count = max(color_count, next_color)

    packs: List[List[T]] = [[] for _index in range(color_count)]
    for index, item in enumerate(items):
        packs[colors[index]].append(item)
    return packs


def _containment_depths(
    locations: Sequence[Tuple[Path, int, int]],
) -> Dict[int, int]:
    by_path: Dict[Path, List[int]] = {}
    for index, (path, _start, _end) in enumerate(locations):
        by_path.setdefault(path, []).append(index)

    depths: Dict[int, int] = {}
    for indices in by_path.values():
        ordered = sorted(
            indices,
            key=lambda value: (
                locations[value][1],
                -locations[value][2],
                value,
            ),
        )
        ends = sorted({locations[index][2] for index in indices}, reverse=True)
        ranks = {end: position + 1 for position, end in enumerate(ends)}
        tree: List[Optional[Tuple[int, int]]] = [None] * (len(ends) + 1)

        for index in ordered:
            _path, start, end = locations[index]
            rank = ranks[end]
            parent: Optional[Tuple[int, int]] = None
            cursor = rank
            while cursor:
                candidate = tree[cursor]
                if candidate is not None and (parent is None or candidate < parent):
                    parent = candidate
                cursor -= cursor & -cursor

            depths[index] = 0 if parent is None else depths[parent[1]] + 1
            value = (end - start, index)
            cursor = rank
            while cursor < len(tree):
                if tree[cursor] is None or value < tree[cursor]:
                    tree[cursor] = value
                cursor += cursor & -cursor
    return depths


def _partition(items: Sequence[T], count: int) -> List[Sequence[T]]:
    if not items:
        return []
    count = max(1, min(count, len(items)))
    base, extra = divmod(len(items), count)
    chunks: List[Sequence[T]] = []
    start = 0
    for index in range(count):
        size = base + (1 if index < extra else 0)
        chunks.append(items[start : start + size])
        start += size
    return chunks
