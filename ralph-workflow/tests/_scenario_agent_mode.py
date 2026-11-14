from __future__ import annotations

from enum import StrEnum

__all__ = ["AgentMode"]


class AgentMode(StrEnum):
    EDITS_AND_STAGES = "edits_and_stages"
    EDITS_NEVER_STAGES = "edits_never_stages"
    LEAVES_MARKER = "leaves_marker"
    NON_ZERO_EXIT = "non_zero_exit"
    SIGNAL = "signal"
    WATCHDOG_HANG = "watchdog_hang"
    PROVIDER_FAILURE = "provider_failure"
    INVALID_RESULT = "invalid_result"
