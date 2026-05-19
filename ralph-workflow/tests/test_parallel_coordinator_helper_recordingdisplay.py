from __future__ import annotations

from collections import defaultdict

from ralph.pipeline.worker_state import WorkerStatus


class RecordingDisplay:
    def __init__(self) -> None:
        self.outputs: dict[str, list[str]] = defaultdict(list)
        self.statuses: dict[str, list[WorkerStatus]] = defaultdict(list)
        self._running_units: set[str] = set()
        self.peak_running = 0

    def emit(self, unit_id: str | None, line: str) -> None:
        if unit_id is None:
            return
        self.outputs[unit_id].append(line)

    def set_status(self, unit_id: str, status: WorkerStatus) -> None:
        self.statuses[unit_id].append(status)
        if status is WorkerStatus.RUNNING:
            self._running_units.add(unit_id)
        elif status in {
            WorkerStatus.SUCCEEDED,
            WorkerStatus.FAILED,
            WorkerStatus.CANCELLED,
        }:
            self._running_units.discard(unit_id)
        self.peak_running = max(self.peak_running, len(self._running_units))
