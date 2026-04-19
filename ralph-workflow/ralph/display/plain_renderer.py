"""Plain line renderer for non-TTY environments."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from ralph.display.snapshot import DashboardSnapshot, snapshot_from_state

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.console import Console

    from ralph.pipeline.state import PipelineState

LEVELS: Final[dict[str, str]] = {
    "development": "INFO",
    "planning": "INFO",
    "review": "INFO",
    "fix": "INFO",
    "complete": "SUCCESS",
    "failed": "ERROR",
    "interrupted": "WARN",
}


class PlainLogRenderer:
    """Emit plain, ANSI-free structured log lines."""

    def __init__(
        self,
        console: Console,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._console = console
        self._clock = clock
        self._last_phase: str | None = None
        self._last_iteration: int | None = None
        self._last_worker_states: dict[str, str] = {}

    def emit_snapshot(self, snapshot: DashboardSnapshot) -> None:
        lines: list[str] = []

        if snapshot.phase != self._last_phase:
            lines.append(
                f"{self._clock().isoformat()} {LEVELS.get(snapshot.phase, 'INFO')} "
                f"[phase] {snapshot.phase}"
            )
            self._last_phase = snapshot.phase
        elif snapshot.iteration != self._last_iteration:
            lines.append(
                f"{self._clock().isoformat()} INFO [progress] iteration "
                f"{snapshot.iteration}/{snapshot.total_iterations}"
            )

        self._last_iteration = snapshot.iteration

        for worker in snapshot.workers:
            previous_status = self._last_worker_states.get(worker.unit_id)
            if previous_status == worker.status:
                continue
            lines.append(
                f"{self._clock().isoformat()} INFO [worker] {worker.unit_id} {worker.status}"
            )
            self._last_worker_states[worker.unit_id] = worker.status

        for line in lines:
            self._console.print(line, markup=False, highlight=False, no_wrap=True)

    def emit_log_line(self, unit_id: str, line: str) -> None:
        self._console.print(
            f"{self._clock().isoformat()} INFO [{unit_id}] {line}",
            markup=False,
            highlight=False,
            no_wrap=True,
        )


class PlainModeAdapter:
    """State subscriber that projects pipeline state to plain log lines."""

    def __init__(
        self,
        console: Console,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._renderer = PlainLogRenderer(console, clock=clock)

    def notify(self, state: PipelineState) -> None:
        self._renderer.emit_snapshot(
            snapshot_from_state(state, prompt_path=None, prompt_preview=(), run_id=None)
        )


__all__ = ["LEVELS", "PlainLogRenderer", "PlainModeAdapter"]
