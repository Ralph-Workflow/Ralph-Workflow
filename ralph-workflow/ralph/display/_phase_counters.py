"""Per-phase activity counter dataclass.

Internal leaf module (wt-007-consolidate-display). Re-exports
:class:`PhaseCounters` from the previous
``ralph.display.plain_renderer._phase_counters`` location.
"""

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
