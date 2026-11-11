
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["WorkerResult"]


@dataclass(frozen=True)
class WorkerResult:

    unit_id: str
    exit_code: int
    final_message: str
    duration_ms: int
