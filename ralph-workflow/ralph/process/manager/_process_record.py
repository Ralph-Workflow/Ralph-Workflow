"""Immutable snapshot of a tracked subprocess."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from ralph.process.manager._process_status import ProcessStatus


@dataclass
class ProcessRecord:
    """A point-in-time record describing a child process managed by Ralph.

    The record is created when the process is spawned and updated as it
    transitions through ``ProcessStatus`` values. Terminal records carry
    ``returncode``, ``ended_at``, and optionally a ``cause`` explaining why
    the process ended.
    """

    pid: int
    pgid: int
    command: tuple[str, ...]
    cwd: str | None
    started_at: datetime
    status: ProcessStatus
    returncode: int | None = None
    ended_at: datetime | None = None
    cause: str | None = None
    failure_message: str | None = None
    label: str | None = None
