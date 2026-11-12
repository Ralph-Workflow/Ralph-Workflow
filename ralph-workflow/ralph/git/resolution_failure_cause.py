from __future__ import annotations

from enum import StrEnum

__all__ = ["ResolutionFailureCause"]


class ResolutionFailureCause(StrEnum):
    NONE = "NONE"
    AGENT_FAILURE = "AGENT_FAILURE"
    UNRESOLVED_CONFLICT = "UNRESOLVED_CONFLICT"
    CONTEXT_FAILURE = "CONTEXT_FAILURE"
    CONFIG_FAILURE = "CONFIG_FAILURE"
