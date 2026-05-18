"""Mode-adaptive character limits for content condensation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _ModeAdaptiveLimits:
    """Mode-adaptive character limits for content condensation."""

    headline_max_chars: int
    condenser_soft_limit: int
    condenser_hard_limit: int
    streaming_checkpoint_chars: int
    thinking_preview_min_chars: int
    tool_result_headline_min_chars: int
