"""Mode-adaptive character limits for content condensation (single-mode)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _ModeAdaptiveLimits:
    """Mode-adaptive character limits for content condensation (single-mode).

    After the wt-028-display consolidation, Ralph Workflow uses a single
    ``default`` mode with one fixed set of limits (no per-mode tier).
    """

    headline_max_chars: int
    condenser_soft_limit: int
    condenser_hard_limit: int
    streaming_checkpoint_chars: int
    thinking_preview_min_chars: int
    tool_result_headline_min_chars: int
