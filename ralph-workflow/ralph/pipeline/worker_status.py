"""Worker status enum for parallel execution."""

from enum import StrEnum


class WorkerStatus(StrEnum):
    """Execution status of a single parallel worker."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


__all__ = ["WorkerStatus"]
