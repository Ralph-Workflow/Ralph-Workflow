from __future__ import annotations

from ralph.pipeline.conflict_resolution.session import (
    remember_dead_surface,
    session_ledger,
)

__all__ = [
    "next_unvisited",
    "remember_dead_surface",
    "session_ledger",
]


def next_unvisited(offset: int, total: int, visited: set[int]) -> int:
    for step in range(1, total + 1):
        candidate = (offset + step) % total
        if candidate not in visited:
            return candidate
    return offset
