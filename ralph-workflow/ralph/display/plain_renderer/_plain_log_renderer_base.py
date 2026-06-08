"""Base class for PlainLogRenderer with snapshot/view methods."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rich.text import Text

from ralph.display.context import DisplayContext
from ralph.display.plain_renderer._constants import (
    _ANSI_ESCAPE,
    _CAT_THEME_KEYS,
    _COMPACT_CAT_BADGES,
    _COMPACT_LEVEL_BADGES,
    _EMPTY_PLAN_SIGNATURE,
    _LEVEL_THEME_KEYS,
    LEVELS,
    _sanitize,
    _strip_markup,
)
from ralph.display.plain_renderer._phase_counters import PhaseCounters as _PhaseCounters

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.console import Console

    from ralph.display.snapshot import PipelineSnapshot

#: A tool activity is "repeated" (coalesced with a "xN" count in the live status)
#: starting from the second consecutive identical call.
_MIN_COALESCE_REPEAT = 2


class _PlainLogRendererBase:
    """Core init, helpers, and snapshot-rendering methods for PlainLogRenderer."""

    def __init__(
        self,
        display_context: DisplayContext,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(display_context, DisplayContext):
            raise TypeError("display_context is required")
        self._ctx = display_context
        self._clock = clock
        self._monotonic = monotonic
        self._last_phase: str | None = None
        self._last_budget_progress: dict[str, int] = {}
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
                str | None,
                int,
            ]
            | None
        ) = None
        self._last_analysis_signature: tuple[str | None, str | None, str | None] | None = None
        self._last_waiting_signature: str | None = None
        self._active_block: dict[str, tuple[str, list[str]]] = {}
        self._last_checkpoint_chars: dict[str, int] = {}
        self._emitted_empty_plan: bool = False
        self._emitted_empty_activity: bool = False
        self._emitted_empty_decision_log: bool = False
        self._phase_counters: _PhaseCounters | None = None
        self._last_phase_saved_counters: _PhaseCounters | None = None
        self._last_phase_elapsed_seconds: float = 0.0
        self._last_phase_artifact_outcome: str = ""
        self._phase_close_emitted: bool = False
        self._run_start_time: float | None = None
        self._run_counters: _PhaseCounters = _PhaseCounters()
        self._last_emitted_tool_signature: dict[str, tuple[str, str]] = {}

    @property
    def _console(self) -> Console:
        return self._ctx.console

    def _format_timestamp(self, ts: datetime) -> str:
        """Format a datetime as a timestamp string, abbreviated in compact mode."""
        if self._ctx.mode == "compact":
            return ts.strftime("%H:%M:%S")
        return ts.isoformat()

    def _build_line(self, timestamp: str, level: str, cat: str, suffix: str) -> Text:
        """Build a styled Text line with level and category badge segments."""
        t = Text()
        t.append(timestamp + " ")
        if self._ctx.mode == "compact":
            level_badge = _COMPACT_LEVEL_BADGES.get(level, level[0])
            cat_badge = _COMPACT_CAT_BADGES.get(cat, cat[0])
        else:
            level_badge = level
            cat_badge = cat
        t.append(level_badge, style=_LEVEL_THEME_KEYS.get(level, ""))
        t.append(" ")
        t.append(cat_badge, style=_CAT_THEME_KEYS.get(cat, ""))
        t.append(" ")
        t.append(suffix)
        return t

    def _snapshot_texts(self, snapshot: PipelineSnapshot) -> list[Text]:
        timestamp = self._format_timestamp(self._clock())
        texts: list[Text] = []
        texts.extend(self._phase_lines(snapshot, timestamp))
        texts.extend(self._plan_lines(snapshot, timestamp))
        texts.extend(self._waiting_lines(snapshot, timestamp))
        texts.extend(self._activity_lines(snapshot, timestamp))
        texts.extend(self._analysis_lines(snapshot, timestamp))
        texts.extend(self._decision_log_lines(snapshot, timestamp))
        texts.extend(self._worker_lines(snapshot, timestamp))
        texts.extend(self._result_lines(snapshot, timestamp))
        return texts

    def snapshot_lines(self, snapshot: PipelineSnapshot) -> list[str]:
        return [t.plain for t in self._snapshot_texts(snapshot)]

    def _phase_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[Text]:
        current_bp = {name: bp.completed for name, bp in snapshot.budget_progress.items()}
        if snapshot.phase != self._last_phase:
            self._last_phase = snapshot.phase
            self._last_budget_progress = current_bp
            role = snapshot.current_phase_role
            if snapshot.is_terminal_failure:
                level = "ERROR"
            elif snapshot.interrupted_by_user:
                level = "WARN"
            else:
                level = LEVELS.get(role, "INFO") if role is not None else "INFO"
            marker = f"{self._ctx.glyph_for('milestone')} " if level == "MILESTONE" else ""
            return [self._build_line(timestamp, level, "META", f"[phase] {marker}{snapshot.phase}")]
        if current_bp != self._last_budget_progress:
            prev_bp = self._last_budget_progress
            self._last_budget_progress = current_bp
            lines = []
            for name, bp in snapshot.budget_progress.items():
                if current_bp.get(name) != prev_bp.get(name):
                    lines.append(
                        self._build_line(
                            timestamp,
                            "INFO",
                            "META",
                            f"[progress] {name} {bp.completed}/{bp.cap}",
                        )
                    )
            return lines
        return []

    def _plan_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[Text]:
        plan_signature: tuple[str | None, tuple[str, ...], int] = (
            snapshot.plan_summary,
            snapshot.plan_scope_items,
            snapshot.plan_total_steps,
        )
        if plan_signature == self._last_plan_signature:
            return []
        self._last_plan_signature = plan_signature

        if plan_signature == _EMPTY_PLAN_SIGNATURE and not self._emitted_empty_plan:
            self._emitted_empty_plan = True
            return [self._build_line(timestamp, "INFO", "META", "[plan] (no plan loaded yet)")]

        texts: list[Text] = []
        if snapshot.plan_summary:
            texts.append(
                self._build_line(
                    timestamp, "INFO", "META", f"[plan] {_sanitize(snapshot.plan_summary)}"
                )
            )
        if snapshot.plan_scope_items:
            scope = " | ".join(_sanitize(item) for item in snapshot.plan_scope_items)
            texts.append(self._build_line(timestamp, "INFO", "META", f"[plan-scope] {scope}"))
        if snapshot.plan_total_steps > 0:
            texts.append(
                self._build_line(
                    timestamp,
                    "INFO",
                    "META",
                    f"[plan-steps] {snapshot.plan_current_step or '—'}/{snapshot.plan_total_steps}",
                )
            )
        return texts

    def _build_activity_parts(self, snapshot: PipelineSnapshot) -> list[str]:
        """Build activity key=value parts from structured fields."""
        parts: list[str] = []
        if snapshot.active_agent:
            parts.append(f"agent={_sanitize(snapshot.active_agent)}")
        if snapshot.active_tool:
            parts.append(f"tool={_sanitize(snapshot.active_tool)}")
        if snapshot.active_path:
            parts.append(f"path={_sanitize(snapshot.active_path)}")
        if snapshot.active_workdir:
            parts.append(f"workdir={_sanitize(snapshot.active_workdir)}")
        if snapshot.active_command:
            parts.append(f"command={_sanitize(snapshot.active_command)}")
        if snapshot.active_pattern:
            parts.append(f"pattern={_sanitize(snapshot.active_pattern)}")
        return parts

    def _waiting_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[Text]:
        line = snapshot.waiting_status_line
        if not line:
            return []
        if line == self._last_waiting_signature:
            return []
        self._last_waiting_signature = line
        sanitized = _sanitize(line)
        if "hit hard ceiling" in sanitized:
            level = "ERROR"
        elif "may be frozen" in sanitized:
            level = "WARN"
        else:
            level = "INFO"
        return [self._build_line(timestamp, level, "META", f"[waiting] {sanitized}")]

    def _activity_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[Text]:
        activity_parts = self._build_activity_parts(snapshot)
        structured_text = " ".join(activity_parts) if activity_parts else None

        # ``active_tool_repeat`` >= 2 means a NEW back-to-back call of the same tool
        # (incremented per call by the subscriber, not per re-render). Such a repeat
        # must refresh the live status with a count instead of being hidden — the
        # bug where the status froze while an agent looped on one tool.
        repeat = snapshot.active_tool_repeat
        is_repeat = repeat >= _MIN_COALESCE_REPEAT

        # Suppress the [activity] line only when it would merely duplicate the
        # just-emitted [tool] CONT line for the SAME single call (first occurrence).
        if not is_repeat and snapshot.active_tool and snapshot.active_path:
            tool_sig = self._last_emitted_tool_signature.get(snapshot.active_unit_id or "")
            if tool_sig is not None:
                last_tool, last_path = tool_sig
                if last_tool == snapshot.active_tool and last_path == snapshot.active_path:
                    return []

        # The repeat count is part of the signature: a re-render with the same count
        # is still deduplicated, but an increased count (a new call) refreshes.
        activity_signature = (
            snapshot.active_agent,
            snapshot.active_tool,
            snapshot.active_path,
            snapshot.active_workdir,
            snapshot.active_command,
            snapshot.active_pattern,
            structured_text,
            repeat,
        )
        if activity_signature == self._last_activity_signature:
            return []
        self._last_activity_signature = activity_signature

        all_none = all(
            v is None
            for v in (
                snapshot.active_agent,
                snapshot.active_tool,
                snapshot.active_path,
                snapshot.active_workdir,
                snapshot.active_command,
                snapshot.active_pattern,
            )
        )
        if all_none and not snapshot.last_activity_line and not self._emitted_empty_activity:
            self._emitted_empty_activity = True
            return [self._build_line(timestamp, "INFO", "META", "[activity] (no active agent yet)")]

        suffix = f" (x{repeat})" if is_repeat else ""
        if snapshot.last_activity_line:
            line_text = _sanitize(snapshot.last_activity_line)
            if snapshot.active_path:
                sanitized_path = _sanitize(snapshot.active_path)
                if sanitized_path not in line_text:
                    line_text = f"{line_text} (path={sanitized_path})"
            return [self._build_line(timestamp, "INFO", "META", f"[activity] {line_text}{suffix}")]

        if activity_parts:
            return [
                self._build_line(
                    timestamp, "INFO", "META", f"[activity] {' '.join(activity_parts)}{suffix}"
                )
            ]
        return []

    def _analysis_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[Text]:
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

        if snapshot.current_phase_role == "analysis":
            return []

        reason = f" — {_sanitize(snapshot.analysis_reason)}" if snapshot.analysis_reason else ""
        return [
            self._build_line(
                timestamp,
                "INFO",
                "META",
                f"[analysis] {snapshot.analysis_phase} {snapshot.analysis_decision}{reason}",
            )
        ]

    def _decision_log_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[Text]:
        if snapshot.decision_log:
            return []
        if snapshot.current_phase_role == "analysis":
            return []
        if self._emitted_empty_decision_log:
            return []
        self._emitted_empty_decision_log = True
        return [
            self._build_line(timestamp, "INFO", "META", "[analysis] (no decisions recorded yet)")
        ]

    def _worker_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[Text]:
        texts: list[Text] = []
        for worker in snapshot.workers:
            previous_status = self._last_worker_states.get(worker.unit_id)
            if previous_status == worker.status:
                continue
            texts.append(
                self._build_line(
                    timestamp, "INFO", "META", f"[worker] {worker.unit_id} {worker.status}"
                )
            )
            self._last_worker_states[worker.unit_id] = worker.status
        return texts

    def _result_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[Text]:
        if snapshot.is_terminal_failure and snapshot.last_error:
            return [
                self._build_line(
                    timestamp,
                    "ERROR",
                    "META",
                    f"[failure] {_sanitize(snapshot.last_error)}",
                )
            ]
        if not snapshot.is_terminal_success:
            return []

        texts = [self._build_line(timestamp, "SUCCESS", "META", "[result] pipeline complete")]
        if snapshot.pr_url:
            texts.append(
                self._build_line(timestamp, "SUCCESS", "META", f"[pr] {_sanitize(snapshot.pr_url)}")
            )
        return texts

    @staticmethod
    def strip_markup(text: str) -> str:
        stripped = _strip_markup(text)
        return _ANSI_ESCAPE.sub("", stripped)

    def emit_snapshot(self, snapshot: PipelineSnapshot) -> None:
        for text in self._snapshot_texts(snapshot):
            self._console.print(text, markup=False, highlight=False, no_wrap=True)
