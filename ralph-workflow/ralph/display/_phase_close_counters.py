from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _PhaseCloseCounters:
    content_blocks: int | None = None
    thinking_blocks: int | None = None
    tool_calls: int | None = None
    errors: int | None = None
