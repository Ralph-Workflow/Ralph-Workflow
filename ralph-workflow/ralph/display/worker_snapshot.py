"""Immutable projection of a single worker's execution state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class WorkerSnapshot:
    """Immutable projection of a single worker's execution state."""

    unit_id: str
    description: str
    status: str
    status_semantic: str
    started_at: datetime | None
    finished_at: datetime | None
    elapsed_s: float
    exit_code: int | None
    error_message: str | None
    dropped_lines: int = 0
