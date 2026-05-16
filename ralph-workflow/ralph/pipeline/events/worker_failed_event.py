from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerFailedEvent:
    """Emitted when a parallel worker terminates with a failure."""

    unit_id: str
    exit_code: int
    error: str
