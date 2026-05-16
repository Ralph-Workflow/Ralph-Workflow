from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerStartedEvent:
    """Emitted when a parallel worker begins execution."""

    unit_id: str
