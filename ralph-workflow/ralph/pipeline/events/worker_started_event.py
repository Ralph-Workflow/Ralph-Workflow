from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerStartedEvent:
    unit_id: str
