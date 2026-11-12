from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerFailedEvent:
    unit_id: str
    exit_code: int
    error: str
