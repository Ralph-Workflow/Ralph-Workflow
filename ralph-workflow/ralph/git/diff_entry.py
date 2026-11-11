
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiffEntry:

    path: str
    status: str
    old_mode: str
    new_mode: str
    size: int
    is_binary: bool
    content: str | None = None
    base_tree_paths: frozenset[str] | None = None
