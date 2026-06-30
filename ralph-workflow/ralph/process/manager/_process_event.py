"""Immutable status-change event emitted by the process manager."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from ralph.process.manager._process_record import ProcessRecord
    from ralph.process.manager._process_status import ProcessStatus


@dataclass(frozen=True)
class ProcessEvent:
    """A single transition in a process record's lifecycle.

    Carries the record that changed, the status before and after the
    transition, and the wall-clock time at which the event was observed.
    """

    record: ProcessRecord
    previous_status: ProcessStatus
    new_status: ProcessStatus
    timestamp: datetime
