from __future__ import annotations

from dataclasses import dataclass
from typing import Final

HEADLINE_MAX_CHARS: Final[int] = 120
CONDENSER_SOFT_LIMIT: Final[int] = 400
CONDENSER_HARD_LIMIT: Final[int] = 4000
STREAMING_CHECKPOINT_CHARS: Final[int] = 4000
THINKING_PREVIEW_MIN_CHARS: Final[int] = 80
TOOL_RESULT_HEADLINE_MIN_CHARS: Final[int] = 80
BODY_MEASURE_CAP: Final[int] = 100


@dataclass(frozen=True)
class _ModeAdaptiveLimits:
    headline_max_chars: int
    condenser_soft_limit: int
    condenser_hard_limit: int
    streaming_checkpoint_chars: int
    thinking_preview_min_chars: int
    tool_result_headline_min_chars: int
    body_measure_cap: int


_DEFAULT_LIMITS: Final[_ModeAdaptiveLimits] = _ModeAdaptiveLimits(
    headline_max_chars=HEADLINE_MAX_CHARS,
    condenser_soft_limit=CONDENSER_SOFT_LIMIT,
    condenser_hard_limit=CONDENSER_HARD_LIMIT,
    streaming_checkpoint_chars=STREAMING_CHECKPOINT_CHARS,
    thinking_preview_min_chars=THINKING_PREVIEW_MIN_CHARS,
    tool_result_headline_min_chars=TOOL_RESULT_HEADLINE_MIN_CHARS,
    body_measure_cap=BODY_MEASURE_CAP,
)


__all__ = [
    "_DEFAULT_LIMITS",
    "_ModeAdaptiveLimits",
]
