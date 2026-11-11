
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChildActivitySnapshot:

    scope_prefix: str
    has_process: bool
    has_fresh_label: bool
    has_fresh_progress: bool
    oldest_live_child_seconds: float | None
    active_count: int
    terminal_count: int
    has_fresh_heartbeat: bool = False


__all__ = ["ChildActivitySnapshot"]
