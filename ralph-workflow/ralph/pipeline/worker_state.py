"""Worker execution state model for parallel pipeline workers."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class WorkerStatus(StrEnum):
    """Execution status of a single parallel worker."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkerState(BaseModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Immutable snapshot of a single parallel worker's execution state.

    Attributes:
        unit_id: Identifier of the work unit this worker is executing.
        status: Current execution status.
        started_at: When the worker started execution.
        finished_at: When the worker finished execution.
        exit_code: Process exit code, if finished.
        error_message: Human-readable error description, if failed.
        commit_sha: Git commit SHA produced by this worker, if any.
        worker_namespace: Filesystem path to the worker's per-worker namespace
            under ``.agent/workers/<unit_id>/`` in the shared checkout.
        log_file: Path to the worker's log file.
    """

    model_config = ConfigDict(frozen=True)

    unit_id: str = Field(..., min_length=1)
    status: WorkerStatus = WorkerStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    error_message: str | None = None
    commit_sha: str | None = None
    worker_namespace: str | None = None
    log_file: str | None = None

    def copy_with(self, **updates: object) -> "WorkerState":
        """Return a copy with the given fields replaced."""
        return self.model_copy(update=updates)
