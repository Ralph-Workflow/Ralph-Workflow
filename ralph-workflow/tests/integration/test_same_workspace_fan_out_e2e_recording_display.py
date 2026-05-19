"""RecordingDisplay helper for test_same_workspace_fan_out_e2e_same_workspace_fan_out_e2_e.py."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.worker_state import WorkerStatus


class RecordingDisplay:
    def __init__(self) -> None:
        self.statuses: dict[str, list[WorkerStatus]] = defaultdict(list)

    def emit(self, unit_id: str | None, line: str) -> None:
        del unit_id, line

    def set_status(self, unit_id: str, status: WorkerStatus) -> None:
        self.statuses[unit_id].append(status)
