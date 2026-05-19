"""Activity counter snapshot for a pipeline phase."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PhaseActivityCounts:
    """Activity counter snapshot for a pipeline phase."""

    content_blocks: int = 0
    thinking_blocks: int = 0
    tool_calls: int = 0
    errors: int = 0
