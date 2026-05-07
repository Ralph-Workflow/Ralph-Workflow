"""Parallel display adapter: always emit log-first, copy-paste-safe transcript lines."""

from __future__ import annotations

import contextlib
import queue
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from ralph.display.activity_router import ActivityRouter
from ralph.display.content_condenser import condense_content
from ralph.display.context import DisplayContext
from ralph.display.lifecycle_filter import is_bare_lifecycle as _is_bare_lifecycle
from ralph.display.long_content_summary import build_headline_or_placeholder
from ralph.display.plain_renderer import PlainLogRenderer, _PhaseCounters
from ralph.display.raw_overflow import RawOverflowLog
from ralph.display.subscriber import PipelineSubscriber
from ralph.display.tool_args import format_tool_input, friendly_tool_name

if TYPE_CHECKING:
    from types import TracebackType

    from rich.console import Console

    from ralph.display.activity_model import ActivityEventKind
    from ralph.display.phase_lifecycle import PhaseExitModel
    from ralph.display.phase_status import PhaseIterationContext
    from ralph.display.plain_renderer import RunStartOrientation
    from ralph.display.snapshot import PipelineSnapshot
    from ralph.pipeline.worker_state import WorkerStatus
    from ralph.policy.models import PipelinePolicy

_DEFAULT_SNAPSHOT_QUEUE_MAXSIZE: int = 64
_MAX_OVERFLOW_FILE_BYTES: int = 50 * 1024 * 1024  # 50 MB guard
_DROP_DEBOUNCE_SECONDS: float = 1.0
_NEVER_WARNED: float = float("-inf")


def _strip_markup(line: str) -> str:
    return PlainLogRenderer.strip_markup(line)


class ParallelDisplay:
    __slots__ = (
        "_activity_router",
        "_ctx",
        "_drop_last_warned",
        "_overflow_logs",
        "_overflow_warned",
        "_plain_renderer",
        "_subscriber",
        "_workspace_root",
    )

    def __init__(
        self,
        display_context: DisplayContext,
        *,
        subscriber: PipelineSubscriber | None = None,
        workspace_root: Path | None = None,
        run_id: str | None = None,
        pipeline_policy: PipelinePolicy | None = None,
    ) -> None:
        if not isinstance(display_context, DisplayContext):
            raise TypeError("display_context is required")
        self._ctx = display_context

        self._plain_renderer = PlainLogRenderer(self._ctx)
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
                pipeline_policy=pipeline_policy,
            )

    @property
    def _console(self) -> Console:
        return self._ctx.console

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
        metadata: dict[str, object],
    ) -> None:
        from ralph.display.activity_model import ActivityEventKind as _Kind  # noqa: PLC0415

        text = content or ""

        tool_signature: tuple[str, str] | None = None

        if kind is _Kind.TOOL_USE:
            original_name = text
            text = friendly_tool_name(text)
            input_obj = metadata.get("input")
            args_str = format_tool_input(input_obj)
            if args_str:
                text = f"{text} {args_str}"
            input_dict: dict[str, object] = (
                cast("dict[str, object]", input_obj) if isinstance(input_obj, dict) else {}
            )
            tool_path = str(input_dict.get("path", "") or "")
            tool_workdir = str(input_dict.get("workdir", "") or "")
            tool_command = str(input_dict.get("command", "") or "")
            tool_pattern = str(input_dict.get("pattern", "") or "")
            tool_signature = (original_name, tool_path)
            with contextlib.suppress(Exception):
                self._subscriber.record_activity(
                    unit_id=unit_id,
                    line=text,
                    tool_name=original_name,
                    path=tool_path or None,
                    workdir=tool_workdir or None,
                    command=tool_command or None,
                    pattern=tool_pattern or None,
                )

        overflow = self._get_overflow_log(unit_id)
        overflow_ref = overflow.relative_reference(self._workspace_root)

        visible, condensed_flag, summary_line, ai_summary_line = condense_content(
            text,
            soft_limit=self._ctx.condenser_soft_limit,
            hard_limit=self._ctx.condenser_hard_limit,
            summary=True,
            env=self._ctx.env,
        )

        if condensed_flag:
            self._check_overflow_size(unit_id, overflow)
            overflow.append(text)

        effective_summary_line = summary_line
        if (
            kind is _Kind.TOOL_RESULT
            and summary_line is None
            and text.strip()
            and len(text) >= self._ctx.tool_result_headline_min_chars
        ):
            effective_summary_line = build_headline_or_placeholder(
                text, max_chars=self._ctx.headline_max_chars
            )

        self._plain_renderer.emit_activity_line(
            unit_id,
            kind.value,
            visible,
            condensed_ref=overflow_ref if condensed_flag else None,
            condensed_flag=condensed_flag,
            summary_line=effective_summary_line,
            ai_summary_line=ai_summary_line,
            _tool_signature=tool_signature,
        )

        self._emit_drop_warning(unit_id)

    @property
    def activity_router(self) -> ActivityRouter:
        return self._activity_router

    @property
    def mode(self) -> Literal["compact", "medium", "wide"]:
        return self._ctx.mode

    @property
    def subscriber(self) -> PipelineSubscriber:
        return self._subscriber

    def start(self) -> None:
        return None

    def stop(self) -> None:
        self._plain_renderer.flush_blocks()

    def emit(self, unit_id: str | None, line: str) -> None:
        """Emit a raw line directly to the plain renderer.

        Bare lifecycle tokens (e.g. prefixed transcript noise) are silently
        dropped before reaching the renderer. If unit_id is None, defaults to "run".
        """
        if _is_bare_lifecycle(line):
            return
        self._plain_renderer.emit_log_line(unit_id or "run", line)

    def emit_parsed_event(
        self,
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        metadata: dict[str, object],
    ) -> None:
        """Route a pre-parsed agent event through the structured activity path."""
        from ralph.display.activity_model import ActivityEventKind as _Kind  # noqa: PLC0415

        if (
            kind in (_Kind.LIFECYCLE, _Kind.UNKNOWN)
            and content is not None
            and _is_bare_lifecycle(content)
        ):
            return
        self._emit_activity_event(unit_id, kind, content, None, metadata)

    def set_status(self, unit_id: str, status: WorkerStatus) -> None:
        self._plain_renderer.emit_status_line(unit_id, str(status))

    def emit_analysis_result(
        self,
        phase: str,
        decision: str,
        reason: str | None = None,
    ) -> None:
        with contextlib.suppress(Exception):
            self._subscriber.record_analysis(phase, decision, reason)

    def emit_run_start(self, orientation: RunStartOrientation) -> None:
        """Emit a one-time run-start orientation block at pipeline start."""
        with contextlib.suppress(Exception):
            self._plain_renderer.emit_run_start(orientation)

    def begin_phase(self, phase: str) -> None:
        """Start timing a new phase and reset its counters."""
        with contextlib.suppress(Exception):
            self._plain_renderer.begin_phase(phase)

    @property
    def last_phase_elapsed_seconds(self) -> float:
        """Return elapsed time of the most recently closed phase in seconds."""
        return self._plain_renderer.last_phase_elapsed_seconds

    @property
    def last_phase_counters(self) -> _PhaseCounters | None:
        """Return the counters from the most recently closed phase, if available.

        Returns None when no phase has been closed yet.
        """
        return self._plain_renderer.last_phase_counters

    @property
    def last_phase_artifact_outcome(self) -> str:
        """Return the artifact outcome from the most recently closed phase."""
        return self._plain_renderer.last_phase_artifact_outcome

    @property
    def phase_close_emitted(self) -> bool:
        """Return True when emit_phase_close_from_exit was called for the current phase."""
        return self._plain_renderer.phase_close_emitted

    def record_artifact_outcome(self, outcome: str) -> None:
        """Record artifact outcome without emitting a log line."""
        with contextlib.suppress(Exception):
            self._plain_renderer.record_artifact_outcome(outcome)

    def emit_phase_close(
        self,
        phase: str,
        produced: str,
        *,
        phase_role: str | None = None,
        iteration_context: PhaseIterationContext | None = None,
        exit_trigger: str | None = None,
    ) -> None:
        """Emit a single-line recap at the end of a phase."""
        with contextlib.suppress(Exception):
            self._plain_renderer.emit_phase_close(
                phase,
                produced,
                phase_role=phase_role,
                iteration_context=iteration_context,
                exit_trigger=exit_trigger,
            )

    def emit_phase_close_from_exit(self, exit_model: PhaseExitModel) -> None:
        """Emit a phase-close recap from a PhaseExitModel."""
        with contextlib.suppress(Exception):
            self._plain_renderer.emit_phase_close_from_exit(exit_model)

    def emit_run_end(
        self,
        *,
        phase: str,
        total_agent_calls: int = 0,
        pr_url: str | None = None,
        exit_trigger: str | None = None,
        outer_dev_iteration: int | None = None,
    ) -> None:
        """Emit a one-time run-end orientation block at pipeline stop."""
        with contextlib.suppress(Exception):
            self._plain_renderer.emit_run_end(
                phase=phase,
                total_agent_calls=total_agent_calls,
                pr_url=pr_url,
                exit_trigger=exit_trigger,
                outer_dev_iteration=outer_dev_iteration,
            )

    @property
    def console(self) -> Console:
        """Expose console for external renderers."""
        return self._ctx.console

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


__all__ = ["ParallelDisplay", "_strip_markup"]
