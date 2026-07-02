"""Mode-adaptive character limits for content condensation (single-mode).

After the wt-028-display consolidation, Ralph Workflow uses a single
``default`` mode with one fixed set of limits (no per-mode tier).
This module is the single owner of the consolidated limits — the
public :data:`_DEFAULT_LIMITS` constant lives here and
:mod:`ralph.display.context` imports it directly so the limit
definition has exactly one home in the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

HEADLINE_MAX_CHARS: Final[int] = 120
CONDENSER_SOFT_LIMIT: Final[int] = 400
CONDENSER_HARD_LIMIT: Final[int] = 4000
STREAMING_CHECKPOINT_CHARS: Final[int] = 4000
THINKING_PREVIEW_MIN_CHARS: Final[int] = 80
TOOL_RESULT_HEADLINE_MIN_CHARS: Final[int] = 80


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


_DEFAULT_LIMITS: Final[_ModeAdaptiveLimits] = _ModeAdaptiveLimits(
    headline_max_chars=HEADLINE_MAX_CHARS,
    condenser_soft_limit=CONDENSER_SOFT_LIMIT,
    condenser_hard_limit=CONDENSER_HARD_LIMIT,
    streaming_checkpoint_chars=STREAMING_CHECKPOINT_CHARS,
    thinking_preview_min_chars=THINKING_PREVIEW_MIN_CHARS,
    tool_result_headline_min_chars=TOOL_RESULT_HEADLINE_MIN_CHARS,
)


__all__ = [
    "_DEFAULT_LIMITS",
    "_ModeAdaptiveLimits",
]
