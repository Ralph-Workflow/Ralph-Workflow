from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _StubPhaseCounters:
    content_blocks: int = 0
    thinking_blocks: int = 0
    tool_calls: int = 0
    errors: int = 0
