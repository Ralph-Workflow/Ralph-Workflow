"""Worker completed event for the pipeline."""

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerCompletedEvent:
    """Emitted when a parallel worker finishes successfully."""

    unit_id: str
    exit_code: int
