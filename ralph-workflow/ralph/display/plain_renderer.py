"""Plain line renderer for non-TTY environments and copy-paste-safe transcripts."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from rich.text import Text

from ralph.display.snapshot import PipelineSnapshot, snapshot_from_state

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

# Closed set of tags for structured log lines
_TAGS: Final[tuple[str, ...]] = (
    "phase",
    "plan",
    "plan-scope",
    "plan-steps",
    "activity",
    "activity-line",
    "analysis",
    "worker",
    "result",
    "pr",
    "failure",
    "artifact",
)

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_markup(text: str) -> str:
    try:
        return Text.from_markup(text).plain
    except Exception:
        return text


def _sanitize(text: str) -> str:
    """Strip both Rich markup and ANSI escapes for copy-paste safety."""
    return _ANSI_ESCAPE.sub("", _strip_markup(text))


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
        self._last_plan_signature: tuple[str | None, tuple[str, ...], int] | None = None
        self._last_activity_signature: (
            tuple[
                str | None,
                str | None,
                str | None,
                str | None,
                str | None,
                str | None,
            ]
            | None
        ) = None
        self._last_analysis_signature: tuple[str | None, str | None, str | None] | None = None

    def snapshot_lines(self, snapshot: PipelineSnapshot) -> list[str]:
        timestamp = self._clock().isoformat()
        lines: list[str] = []
        lines.extend(self._phase_lines(snapshot, timestamp))
        lines.extend(self._plan_lines(snapshot, timestamp))
        lines.extend(self._activity_lines(snapshot, timestamp))
        lines.extend(self._analysis_lines(snapshot, timestamp))
        lines.extend(self._worker_lines(snapshot, timestamp))
        lines.extend(self._result_lines(snapshot, timestamp))
        return lines

    def _phase_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[str]:
        if snapshot.phase != self._last_phase:
            self._last_phase = snapshot.phase
            self._last_iteration = snapshot.iteration
            return [f"{timestamp} {LEVELS.get(snapshot.phase, 'INFO')} [phase] {snapshot.phase}"]
        if snapshot.iteration != self._last_iteration:
            self._last_iteration = snapshot.iteration
            return [
                f"{timestamp} INFO [progress] iteration "
                f"{snapshot.iteration}/{snapshot.total_iterations}"
            ]
        return []

    def _plan_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[str]:
        plan_signature = (
            snapshot.plan_summary,
            snapshot.plan_scope_items,
            snapshot.plan_total_steps,
        )
        if plan_signature == self._last_plan_signature:
            return []
        self._last_plan_signature = plan_signature

        lines: list[str] = []
        if snapshot.plan_summary:
            lines.append(f"{timestamp} INFO [plan] {snapshot.plan_summary}")
        if snapshot.plan_scope_items:
            scope = " | ".join(snapshot.plan_scope_items)
            lines.append(f"{timestamp} INFO [plan-scope] {scope}")
        if snapshot.plan_total_steps > 0:
            lines.append(
                f"{timestamp} INFO [plan-steps] "
                f"{snapshot.plan_current_step or '—'}/{snapshot.plan_total_steps}"
            )
        return lines

    def _activity_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[str]:
        activity_signature = (
            snapshot.active_agent,
            snapshot.active_tool,
            snapshot.active_path,
            snapshot.active_workdir,
            snapshot.active_command,
            snapshot.last_activity_line,
        )
        if activity_signature == self._last_activity_signature:
            return []
        self._last_activity_signature = activity_signature

        activity_parts: list[str] = []
        if snapshot.active_agent:
            activity_parts.append(f"agent={snapshot.active_agent}")
        if snapshot.active_tool:
            activity_parts.append(f"tool={snapshot.active_tool}")
        if snapshot.active_path:
            activity_parts.append(f"path={snapshot.active_path}")
        if snapshot.active_workdir:
            activity_parts.append(f"workdir={snapshot.active_workdir}")
        if snapshot.active_command:
            activity_parts.append(f"command={snapshot.active_command}")

        lines: list[str] = []
        if activity_parts:
            lines.append(f"{timestamp} INFO [activity] {' '.join(activity_parts)}")
        if snapshot.last_activity_line:
            lines.append(f"{timestamp} INFO [activity-line] {snapshot.last_activity_line}")
        return lines

    def _analysis_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[str]:
        analysis_signature = (
            snapshot.analysis_phase,
            snapshot.analysis_decision,
            snapshot.analysis_reason,
        )
        if analysis_signature == self._last_analysis_signature:
            return []
        self._last_analysis_signature = analysis_signature

        if not snapshot.analysis_phase or not snapshot.analysis_decision:
            return []

        # Suppress the [analysis] line for development_analysis and review_analysis
        # phases because render_analysis_decision already outputs a titled block
        # for these phases. We only emit [analysis] lines for other snapshot
        # sources that don't have their own titled-block renderer.
        if snapshot.analysis_phase in ("development_analysis", "review_analysis"):
            return []

        reason = f" — {snapshot.analysis_reason}" if snapshot.analysis_reason else ""
        return [
            f"{timestamp} INFO [analysis] "
            f"{snapshot.analysis_phase} {snapshot.analysis_decision}{reason}"
        ]

    def _worker_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[str]:
        lines: list[str] = []
        for worker in snapshot.workers:
            previous_status = self._last_worker_states.get(worker.unit_id)
            if previous_status == worker.status:
                continue
            lines.append(f"{timestamp} INFO [worker] {worker.unit_id} {worker.status}")
            self._last_worker_states[worker.unit_id] = worker.status
        return lines

    def _result_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[str]:
        if snapshot.phase == "failed" and snapshot.last_error:
            return [f"{timestamp} ERROR [failure] {snapshot.last_error}"]
        if snapshot.phase != "complete":
            return []

        lines = [f"{timestamp} SUCCESS [result] pipeline complete"]
        if snapshot.pr_url:
            lines.append(f"{timestamp} SUCCESS [pr] {snapshot.pr_url}")
        return lines

    @staticmethod
    def strip_markup(text: str) -> str:
        # First try rich's markup stripping
        stripped = _strip_markup(text)
        # Then strip any residual ANSI escape sequences
        return _ANSI_ESCAPE.sub("", stripped)

    def emit_snapshot(self, snapshot: PipelineSnapshot) -> None:
        for line in self.snapshot_lines(snapshot):
            # Strip any ANSI escapes to ensure copy-paste safety
            clean_line = _ANSI_ESCAPE.sub("", line)
            self._console.print(clean_line, markup=False, highlight=False, no_wrap=True)

    def emit_log_line(self, unit_id: str, line: str) -> None:
        sanitized = _sanitize(line)
        self._console.out(f"[{unit_id}] {sanitized}")

    def emit_status_line(self, unit_id: str, status: str) -> None:
        sanitized = _sanitize(status)
        self._console.out(f"[{unit_id}] status={sanitized}")

    def emit_artifact(self, kind: str, summary: str) -> None:
        """Emit an artifact summary line for copy-paste-safe transcripts."""
        timestamp = self._clock().isoformat()
        line = f"{timestamp} INFO [artifact] kind={kind} summary={summary}"
        clean_line = _ANSI_ESCAPE.sub("", line)
        self._console.out(clean_line)


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


__all__ = ["LEVELS", "_TAGS", "PlainLogRenderer", "PlainModeAdapter"]
