"""Plain line renderer for non-TTY environments and copy-paste-safe transcripts."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from rich.text import Text

from ralph.display.long_content_summary import build_content_summary
from ralph.display.snapshot import PipelineSnapshot, snapshot_from_state

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.console import Console

    from ralph.pipeline.state import PipelineState

LEVELS: Final[dict[str, str]] = {
    "development": "MILESTONE",
    "planning": "MILESTONE",
    "review": "MILESTONE",
    "fix": "MILESTONE",
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
    "content",
    "thinking",
    "tool",
    "tool-result",
    "error",
    "progress",
    "status-content",
    "content-start",
    "content-continue",
    "content-end",
    "thinking-start",
    "thinking-continue",
    "thinking-end",
)

# Maps activity kind strings to their log tag
_KIND_TO_TAG: Final[dict[str, str]] = {
    "text": "content",
    "thinking": "thinking",
    "tool_use": "tool",
    "tool_result": "tool-result",
    "error": "error",
    "progress": "progress",
    "status": "status-content",
    "lifecycle": "status-content",
    "raw": "content",
}

_KIND_TO_LEVEL: Final[dict[str, str]] = {
    "error": "ERROR",
    "tool_result": "SUCCESS",
    "progress": "INFO",
    "thinking": "INFO",
    "tool_use": "INFO",
    "lifecycle": "MILESTONE",
    "status": "INFO",
}

# Maps tag to display category prefix META or CONT
_TAG_CATEGORY: Final[dict[str, str]] = {
    "phase": "META",
    "plan": "META",
    "plan-scope": "META",
    "plan-steps": "META",
    "activity": "META",
    "activity-line": "META",
    "worker": "META",
    "analysis": "META",
    "result": "META",
    "pr": "META",
    "failure": "META",
    "artifact": "META",
    "progress": "META",
    "content": "CONT",
    "thinking": "CONT",
    "tool": "CONT",
    "tool-result": "CONT",
    "error": "CONT",
    "status-content": "CONT",
    "content-start": "CONT",
    "content-continue": "CONT",
    "content-end": "CONT",
    "thinking-start": "CONT",
    "thinking-continue": "CONT",
    "thinking-end": "CONT",
}

# Kinds that form streaming blocks
_STREAMING_KINDS: Final[frozenset[str]] = frozenset({"text", "thinking"})

# Kinds that are used for streaming block tags (base tag -> (start, continue, end))
_STREAMING_BLOCK_TAGS: Final[dict[str, tuple[str, str, str]]] = {
    "content": ("content-start", "content-continue", "content-end"),
    "thinking": ("thinking-start", "thinking-continue", "thinking-end"),
}

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")

_EMPTY_PLAN_SIGNATURE: tuple[None, tuple[str, ...], int] = (None, (), 0)


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
        # Global single-block streaming state: at most one unit has an active block.
        # _active_block maps unit_id -> (base_tag, accumulated_content).
        # Invariant: len(_active_block) <= 1.
        self._active_block: dict[str, tuple[str, list[str]]] = {}
        # One-shot flags for empty-state placeholders
        self._emitted_empty_plan: bool = False
        self._emitted_empty_activity: bool = False
        self._emitted_empty_decision_log: bool = False

    def snapshot_lines(self, snapshot: PipelineSnapshot) -> list[str]:
        timestamp = self._clock().isoformat()
        lines: list[str] = []
        lines.extend(self._phase_lines(snapshot, timestamp))
        lines.extend(self._plan_lines(snapshot, timestamp))
        lines.extend(self._activity_lines(snapshot, timestamp))
        lines.extend(self._analysis_lines(snapshot, timestamp))
        lines.extend(self._decision_log_lines(snapshot, timestamp))
        lines.extend(self._worker_lines(snapshot, timestamp))
        lines.extend(self._result_lines(snapshot, timestamp))
        return lines

    def _phase_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[str]:
        if snapshot.phase != self._last_phase:
            self._last_phase = snapshot.phase
            self._last_iteration = snapshot.iteration
            level = LEVELS.get(snapshot.phase, "INFO")
            cat = "META"
            marker = "◆ " if level == "MILESTONE" else ""
            return [f"{timestamp} {level} {cat} [phase] {marker}{snapshot.phase}"]
        if snapshot.iteration != self._last_iteration:
            self._last_iteration = snapshot.iteration
            return [
                f"{timestamp} INFO META [progress] iteration "
                f"{snapshot.iteration}/{snapshot.total_iterations}"
            ]
        return []

    def _plan_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[str]:
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
            return [f"{timestamp} INFO META [plan] (no plan loaded yet)"]

        lines: list[str] = []
        if snapshot.plan_summary:
            lines.append(f"{timestamp} INFO META [plan] {snapshot.plan_summary}")
        if snapshot.plan_scope_items:
            scope = " | ".join(snapshot.plan_scope_items)
            lines.append(f"{timestamp} INFO META [plan-scope] {scope}")
        if snapshot.plan_total_steps > 0:
            lines.append(
                f"{timestamp} INFO META [plan-steps] "
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

        all_none = all(v is None for v in activity_signature)
        if all_none and not self._emitted_empty_activity:
            self._emitted_empty_activity = True
            return [f"{timestamp} INFO META [activity] (no active agent yet)"]

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
            lines.append(f"{timestamp} INFO META [activity] {' '.join(activity_parts)}")
        if snapshot.last_activity_line:
            lines.append(f"{timestamp} INFO META [activity-line] {snapshot.last_activity_line}")
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

        # Suppress [analysis] for development_analysis and review_analysis phases
        # because render_analysis_decision already outputs a titled block for these.
        if snapshot.analysis_phase in ("development_analysis", "review_analysis"):
            return []

        reason = f" — {snapshot.analysis_reason}" if snapshot.analysis_reason else ""
        return [
            f"{timestamp} INFO META [analysis] "
            f"{snapshot.analysis_phase} {snapshot.analysis_decision}{reason}"
        ]

    def _decision_log_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[str]:
        if snapshot.decision_log:
            return []
        if snapshot.phase in ("development_analysis", "review_analysis"):
            return []
        if self._emitted_empty_decision_log:
            return []
        self._emitted_empty_decision_log = True
        return [f"{timestamp} INFO META [analysis] (no decisions recorded yet)"]

    def _worker_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[str]:
        lines: list[str] = []
        for worker in snapshot.workers:
            previous_status = self._last_worker_states.get(worker.unit_id)
            if previous_status == worker.status:
                continue
            lines.append(f"{timestamp} INFO META [worker] {worker.unit_id} {worker.status}")
            self._last_worker_states[worker.unit_id] = worker.status
        return lines

    def _result_lines(self, snapshot: PipelineSnapshot, timestamp: str) -> list[str]:
        if snapshot.phase == "failed" and snapshot.last_error:
            return [f"{timestamp} ERROR META [failure] {snapshot.last_error}"]
        if snapshot.phase != "complete":
            return []

        lines = [f"{timestamp} SUCCESS META [result] pipeline complete"]
        if snapshot.pr_url:
            lines.append(f"{timestamp} SUCCESS META [pr] {snapshot.pr_url}")
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

    def _close_block(self, unit_id: str, timestamp: str) -> str | None:
        """Close an active streaming block and return the end-line, or None if no block."""
        if unit_id not in self._active_block:
            return None
        base_tag, accumulated = self._active_block.pop(unit_id)
        block_tags = _STREAMING_BLOCK_TAGS.get(base_tag)
        if block_tags is None:
            return None
        end_tag = block_tags[2]
        n = len(accumulated)
        chars = sum(len(x) for x in accumulated)
        summary = build_content_summary(" ".join(accumulated), max_chars=120)
        prefix = f"{timestamp} INFO CONT [{end_tag}][{unit_id}] ({n} fragments, {chars} chars)"
        if summary:
            return f"{prefix} {summary}"
        return prefix

    def flush_blocks(self) -> None:
        """Close all open streaming blocks. Call on phase transitions and stop."""
        timestamp = self._clock().isoformat()
        unit_ids = list(self._active_block.keys())
        for unit_id in unit_ids:
            end_line = self._close_block(unit_id, timestamp)
            if end_line:
                self._console.print(end_line, markup=False, highlight=False, no_wrap=True)

    def emit_activity_line(  # noqa: PLR0912, PLR0913
        self,
        unit_id: str,
        kind: str,
        content: str,
        *,
        condensed_ref: str | None = None,
        condensed_flag: bool = False,
        summary_line: str | None = None,
    ) -> None:
        """Emit a kind-tagged, level-badged content line."""
        timestamp = self._clock().isoformat()
        base_tag = _KIND_TO_TAG.get(kind, "content")
        level = _KIND_TO_LEVEL.get(kind, "INFO")
        cat = _TAG_CATEGORY.get(base_tag, "META")
        sanitized = _sanitize(content)
        if condensed_ref is not None and condensed_flag:
            sanitized = f"{sanitized} [see {condensed_ref}]"

        if kind in _STREAMING_KINDS:
            block_tags = _STREAMING_BLOCK_TAGS.get(base_tag)
            if block_tags is not None:
                start_tag, continue_tag, _end_tag = block_tags
                # Global single-block invariant: close any block from a different unit first.
                other_units = [uid for uid in self._active_block if uid != unit_id]
                for other_uid in other_units:
                    end_line = self._close_block(other_uid, timestamp)
                    if end_line:
                        self._console.print(end_line, markup=False, highlight=False, no_wrap=True)
                if unit_id not in self._active_block:
                    # Open new block
                    self._active_block[unit_id] = (base_tag, [content])
                    tag = start_tag
                else:
                    existing_base_tag, accumulated = self._active_block[unit_id]
                    if existing_base_tag != base_tag:
                        # Different kind: close old block first
                        end_line = self._close_block(unit_id, timestamp)
                        if end_line:
                            self._console.print(
                                end_line, markup=False, highlight=False, no_wrap=True
                            )
                        # Open new block
                        self._active_block[unit_id] = (base_tag, [content])
                        tag = start_tag
                    else:
                        # Sequence number: 1-based, computed before appending
                        seq = len(accumulated) + 1
                        accumulated.append(content)
                        tag = f"{continue_tag}#{seq}"
            else:
                tag = base_tag
        else:
            # Non-streaming kind: close ALL open blocks (any unit) before emitting.
            all_units = list(self._active_block.keys())
            for uid in all_units:
                end_line = self._close_block(uid, timestamp)
                if end_line:
                    self._console.print(end_line, markup=False, highlight=False, no_wrap=True)
            tag = base_tag

        if summary_line is not None:
            summary_text = _sanitize(summary_line)
            self._console.print(
                f"{timestamp} INFO {cat} [{tag}][{unit_id}] ↳ summary: {summary_text}",
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        line = f"{timestamp} {level} {cat} [{tag}][{unit_id}] {sanitized}"
        self._console.print(line, markup=False, highlight=False, no_wrap=True)

    def emit_log_line(self, unit_id: str, line: str) -> None:
        self.emit_activity_line(unit_id, "raw", line)

    def emit_status_line(self, unit_id: str, status: str) -> None:
        sanitized = _sanitize(status)
        self._console.out(f"[{unit_id}] status={sanitized}")

    def emit_artifact(self, kind: str, summary: str) -> None:
        """Emit an artifact summary line for copy-paste-safe transcripts."""
        timestamp = self._clock().isoformat()
        line = f"{timestamp} INFO META [artifact] kind={kind} summary={summary}"
        clean_line = _ANSI_ESCAPE.sub("", line)
        self._console.out(clean_line)

    def emit_warn_line(self, unit_id: str, tag: str, message: str) -> None:
        """Emit a WARN META line for a specific tag."""
        timestamp = self._clock().isoformat()
        cat = _TAG_CATEGORY.get(tag, "META")
        line = f"{timestamp} WARN {cat} [{tag}][{unit_id}] {message}"
        self._console.print(line, markup=False, highlight=False, no_wrap=True)


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
