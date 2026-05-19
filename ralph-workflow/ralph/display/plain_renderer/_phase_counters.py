"""Per-phase activity counter dataclass."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PhaseCounters:
    """Per-phase activity counters."""

    content_blocks: int = 0
    thinking_blocks: int = 0
    tool_calls: int = 0
    errors: int = 0
    start_time: float = 0.0
