"""_ConcurrentOperation dataclass for detected concurrent git operations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _ConcurrentOperation:
    kind: str
    description: str


__all__ = ["_ConcurrentOperation"]
