
from datetime import datetime

from pydantic import ConfigDict, Field

from ralph.pipeline.worker_status import WorkerStatus
from ralph.pydantic_compat import RalphBaseModel


class WorkerState(RalphBaseModel):

    model_config = ConfigDict(frozen=True, extra="ignore")

    unit_id: str = Field(..., min_length=1)
    status: WorkerStatus = WorkerStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    error_message: str | None = None
    worker_namespace: str | None = None
    log_file: str | None = None

    def copy_with(self, **updates: object) -> "WorkerState":
        return self.model_copy(update=updates)


__all__ = ["WorkerState", "WorkerStatus"]
