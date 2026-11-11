
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionResultEvent:

    phase: str
    status: str
