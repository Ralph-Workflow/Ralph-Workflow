from __future__ import annotations

from enum import StrEnum

__all__ = ["RungPostCondition"]


class RungPostCondition(StrEnum):
    BLOCKING_OBSERVATION_CHANGED = "blocking_observation_changed"
    DURABLE_COPY_CREATED = "durable_copy_created"
    STOP_GONE = "stop_gone"
    TIP_MOVED = "tip_moved"
    BRANCH_LANDED = "branch_landed"
