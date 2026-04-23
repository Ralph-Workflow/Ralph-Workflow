"""Parallel display adapter: always emit log-first, copy-paste-safe transcript lines."""

from __future__ import annotations

import contextlib
import os
import queue
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ralph.display.activity_router import ActivityRouter
from ralph.display.completion_summary import emit_completion_summary
from ralph.display.content_condenser import condense_content
from ralph.display.mode import NARROW_THRESHOLD, detect_mode
from ralph.display.phase_banner import show_phase_transition
from ralph.display.plain_renderer import PlainLogRenderer
from ralph.display.raw_overflow import RawOverflowLog
from ralph.display.subscriber import PipelineSubscriber
from ralph.display.theme import make_console as _make_console

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

    from rich.console import Console

    from ralph.display.activity_model import ActivityEventKind
    from ralph.display.plain_renderer import RunStartOrientation
    from ralph.display.snapshot import PipelineSnapshot
    from ralph.pipeline.worker_state import WorkerStatus

_DEFAULT_SNAPSHOT_QUEUE_MAXSIZE: int = 64
_MAX_OVERFLOW_FILE_BYTES: int = 50 * 1024 * 1024  # 50 MB guard
_DROP_DEBOUNCE_SECONDS: float = 1.0
_NEVER_WARNED: float = float("-inf")


def _strip_markup(line: str) -> str:
    return PlainLogRenderer.strip_markup(line)


class ParallelDisplay:
    __slots__ = (
        "_activity_router",
        "_console",
        "_drop_last_warned",
        "_mode",
        "_overflow_logs",
        "_overflow_warned",
        "_plain_renderer",
        "_subscriber",
        "_workspace_root",
    )

    def __init__(  # noqa: PLR0913
        self,
        console: Console | None = None,
        env: Mapping[str, str] | None = None,
        *,
        mode: Literal["lines"] | None = None,
        subscriber: PipelineSubscriber | None = None,
        workspace_root: Path | None = None,
        run_id: str | None = None,
    ) -> None:
        resolved_env = dict(os.environ if env is None else env)
        if console is None:
            console = _make_console()
        self._console = console
        self._mode: Literal["lines"] = "lines"
        if mode is None:
            self._mode = detect_mode(console, resolved_env)

        self._plain_renderer = PlainLogRenderer(console)
        self._workspace_root: Path = workspace_root if workspace_root is not None else Path.cwd()

        # Per-unit raw overflow logs, lazy-created on first oversized emit
        self._overflow_logs: dict[str, RawOverflowLog] = {}
        # Track units where the 50 MB guard WARN was already emitted
        self._overflow_warned: set[str] = set()
        # Per-unit last drop-warning timestamp; _NEVER_WARNED means never warned yet
        self._drop_last_warned: dict[str, float] = {}

        self._activity_router: ActivityRouter = ActivityRouter(
            on_event=self._emit_activity_event,
            raw_overflow_callback=self._raw_overflow_write,
        )

        if subscriber is not None:
            self._subscriber = subscriber
        else:
            snapshot_q: queue.Queue[PipelineSnapshot] = queue.Queue(
                maxsize=_DEFAULT_SNAPSHOT_QUEUE_MAXSIZE
            )
            effective_run_id = run_id if run_id is not None else str(uuid.uuid4())
            self._subscriber = PipelineSubscriber(
                queue=snapshot_q,
                workspace_root=self._workspace_root,
                run_id=effective_run_id,
                on_snapshot=self._plain_renderer.emit_snapshot,
            )

    def _get_overflow_log(self, unit_id: str) -> RawOverflowLog:
        if unit_id not in self._overflow_logs:
            self._overflow_logs[unit_id] = RawOverflowLog(self._workspace_root, unit_id)
        return self._overflow_logs[unit_id]

    def _raw_overflow_write(self, unit_id: str, raw_line: str) -> None:
        """Write a raw malformed line to the per-unit overflow log for diagnosis."""
        overflow = self._get_overflow_log(unit_id)
        overflow.append(raw_line)

    def _check_overflow_size(self, unit_id: str, overflow: RawOverflowLog) -> None:
        """Emit a single WARN and disable the log if it exceeds the size guard."""
        if unit_id in self._overflow_warned:
            return
        with contextlib.suppress(OSError):
            if overflow.path.exists() and overflow.path.stat().st_size >= _MAX_OVERFLOW_FILE_BYTES:
                self._overflow_warned.add(unit_id)
                overflow.disable()
                self._plain_renderer.emit_activity_line(
                    unit_id,
                    "progress",
                    f"[overflow log full, raw content for {unit_id} discarded]",
                )

    def _emit_drop_warning(self, unit_id: str) -> None:
        """Check and emit a debounced WARN for dropped ring-buffer lines."""
        buffer = self._activity_router.get_buffer(unit_id)
        delta = buffer.consume_drop_delta()
        if delta <= 0:
            return
        now = time.monotonic()
        last = self._drop_last_warned.get(unit_id, _NEVER_WARNED)
        if now - last < _DROP_DEBOUNCE_SECONDS:
            return
        self._drop_last_warned[unit_id] = now
        self._plain_renderer.emit_warn_line(
            unit_id,
            "progress",
            f"dropped {delta} lines since last flush",
        )

    def _emit_activity_event(
        self,
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
    ) -> None:
        text = content or ""
        overflow = self._get_overflow_log(unit_id)
        overflow_ref = overflow.relative_reference(self._workspace_root)

        # Condenser runs without overflow_ref so it does not embed the path.
        # The renderer receives condensed_ref and appends [see path] when condensed.
        visible, condensed_flag, summary_line, ai_summary_line = condense_content(
            text, soft_limit=400, hard_limit=4000, summary=True
        )

        if condensed_flag:
            self._check_overflow_size(unit_id, overflow)
            overflow.append(text)

        self._plain_renderer.emit_activity_line(
            unit_id,
            kind.value,
            visible,
            condensed_ref=overflow_ref if condensed_flag else None,
            condensed_flag=condensed_flag,
            summary_line=summary_line,
            ai_summary_line=ai_summary_line,
        )

        self._emit_drop_warning(unit_id)

    @property
    def activity_router(self) -> ActivityRouter:
        return self._activity_router

    @property
    def mode(self) -> Literal["lines"]:
        return self._mode

    @property
    def subscriber(self) -> PipelineSubscriber:
        return self._subscriber

    def start(self) -> None:
        return None

    def stop(self) -> None:
        self._plain_renderer.flush_blocks()

    def emit(self, unit_id: str | None, line: str) -> None:
        """Emit a raw line directly. Used as legacy fallback when router is not in play."""
        self._plain_renderer.emit_log_line(unit_id or "activity", line)

    def set_status(self, unit_id: str, status: WorkerStatus) -> None:
        self._plain_renderer.emit_status_line(unit_id, str(status))

    def emit_analysis_result(
        self,
        phase: str,
        decision: str,
        reason: str | None = None,
    ) -> None:
        # Only record to decision_log via subscriber; the titled block is rendered
        # by render_analysis_decision in the phase handler (development.py/review.py).
        # This avoids double-rendering both a plain [analysis] line and a titled block.
        with contextlib.suppress(Exception):
            self._subscriber.record_analysis(phase, decision, reason)

    def emit_phase_transition(self, from_phase: str, to_phase: str) -> None:
        self._plain_renderer.flush_blocks()
        show_phase_transition(from_phase, to_phase, console=self._console)
        with contextlib.suppress(Exception):
            self._subscriber.record_phase_transition(from_phase, to_phase)

    def emit_run_start(self, orientation: RunStartOrientation) -> None:
        """Emit a one-time run-start orientation block at pipeline start."""
        with contextlib.suppress(Exception):
            self._plain_renderer.emit_run_start(orientation)

    def begin_phase(self, phase: str) -> None:
        """Start timing a new phase and reset its counters."""
        with contextlib.suppress(Exception):
            self._plain_renderer.begin_phase(phase)

    def emit_phase_close(self, phase: str, produced: str) -> None:
        """Emit a single-line recap at the end of a phase."""
        with contextlib.suppress(Exception):
            self._plain_renderer.emit_phase_close(phase, produced)

    def emit_run_end(
        self,
        *,
        phase: str,
        total_agent_calls: int = 0,
        pr_url: str | None = None,
    ) -> None:
        """Emit a one-time run-end orientation block at pipeline stop."""
        with contextlib.suppress(Exception):
            self._plain_renderer.emit_run_end(
                phase=phase,
                total_agent_calls=total_agent_calls,
                pr_url=pr_url,
            )
        if phase in {"complete", "failed"}:
            last_state = self._subscriber.last_state
            if last_state is not None:
                with contextlib.suppress(Exception):
                    snapshot = self._subscriber.build_snapshot(last_state)
                    if snapshot is not None:
                        emit_completion_summary(
                            self._console,
                            snapshot,
                            workspace_root=self._workspace_root,
                            dropped_count=self._subscriber.dropped_count,
                        )

    @property
    def console(self) -> Console:
        """Expose console for external renderers."""
        return self._console

    def __enter__(self) -> ParallelDisplay:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        del exc_type, exc_val, exc_tb
        self.stop()


__all__ = ["NARROW_THRESHOLD", "ParallelDisplay", "_strip_markup", "detect_mode"]
