
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerCompletedEvent:

    unit_id: str
    exit_code: int
