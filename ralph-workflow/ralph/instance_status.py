"""Workflow instance lifecycle status values."""

from enum import StrEnum


class InstanceStatus(StrEnum):
    """Lifecycle status of a Ralph Workflow instance."""

    NOT_STARTED = "not_started"
    ACTIVE = "active"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"


__all__ = ["InstanceStatus"]
