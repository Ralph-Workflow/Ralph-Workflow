
from __future__ import annotations

from enum import StrEnum

__all__ = ["WaitReason"]


class WaitReason(StrEnum):

    ALL_AGENTS_UNAVAILABLE = "all_agents_unavailable"
    EXHAUSTED_RETRY = "exhausted_retry"
    COOLDOWN = "cooldown"
