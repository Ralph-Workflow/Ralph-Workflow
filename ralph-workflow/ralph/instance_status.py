from enum import StrEnum


class InstanceStatus(StrEnum):
    NOT_STARTED = "not_started"
    ACTIVE = "active"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"


__all__ = ["InstanceStatus"]
