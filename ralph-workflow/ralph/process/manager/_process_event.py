"""ProcessEvent dataclass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from ralph.process.manager._process_record import ProcessRecord
    from ralph.process.manager._process_status import ProcessStatus


@dataclass(frozen=True)
class ProcessEvent:
    record: ProcessRecord
    previous_status: ProcessStatus
    new_status: ProcessStatus
    timestamp: datetime
