"""Plain line renderer for non-TY environments and copy-paste-safe transcripts."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from rich.text import Text

from ralph.display.context import DisplayContext
from ralph.display.long_content_summary import build_ai_summary, build_headline_or_placeholder
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
    "phase-close",
    "plan",
    "plan-scope",
    "plan-steps",
    "activity",
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
    "run-start",
    "run-end",
    "waiting",
    "status-content",
    "content-start",
    "content-continue",
    "content-end",
    "content-checkpoint",
    "thinking-start",
    "thinking-continue",
    "thinking-end",
    "thinking-checkpoint",
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
    "phase-close": "META",
    "plan": "META",
    "plan-scope": "META",
    "plan-steps": "META",
    "activity": "META",
    "worker": "META",
    "analysis": "META",
    "result": "META",
    "pr": "META",
    "failure": "META",
    "artifact": "META",
    "progress": "META",
    "run-start": "META",
    "run-end": "META",
    "waiting": "META",
    "content": "CONT",
    "thinking": "CONT",
    "tool": "CONT",
    "tool-result": "CONT",
    "error": "CONT",
    "status-content": "CONT",
    "content-start": "CONT",
    "content-continue": "CONT",
    "content-end": "CONT",
    "content-checkpoint": "CONT",
    "thinking-start": "CONT",
    "thinking-continue": "CONT",
    "thinking-end": "CONT",
    "thinking-checkpoint": "CONT",
}

# Theme keys for level badge styling
_LEVEL_THEME_KEYS: Final[dict[str, str]] = {
    "INFO": "theme.level.info",
    "SUCCESS": "theme.level.success",
    "WARN": "theme.level.warn",
    "ERROR": "theme.level.error",
    "MILESTONE": "theme.level.milestone",
}

# Theme keys for category badge styling
_CAT_THEME_KEYS: Final[dict[str, str]] = {
    "META": "theme.cat.meta",
    "CONT": "theme.cat.cont",
}

# Abbreviated badges for compact mode
_COMPACT_LEVEL_BADGES: Final[dict[str, str]] = {
    "INFO": "I",
    "SUCCESS": "S",
    "WARN": "W",
    "ERROR": "E",
    "MILESTONE": "M",
}
_COMPACT_CAT_BADGES: Final[dict[str, str]] = {
    "META": "M",
    "CONT": "C",
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


@dataclass
class _PhaseCounters:
    """Per-phase activity counters."""

    content_blocks: int = 0
    thinking_blocks: int = 0
    tool_calls: int = 0
    errors: int = 0
    start_time: float = 0.0


@dataclass(frozen=True)
class RunStartOrientation:
    """Orientation data emitted once at pipeline start as a structured block."""

    prompt_path: str | None = None
    developer_agent: str | None = None
    developer_model: str | None = None
    reviewer_agent: str | None = None
    reviewer_model: str | None = None
    developer_iters: int | None = None
    reviewer_reviews: int | None = None
    parallel_max_workers: int | None = None
    plan_present: bool = False
    verbosity: str | None = None
    workspace_root: str | None = None
    legend_enabled: bool = field(default=True)


class PlainLogRenderer:
    """Emit plain, ANSI-free structured log lines."""

    def __init__(
        self,
        display_context: DisplayContext,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not isinstance(display_context, DisplayContext):
            raise TypeError("display_context is required")
        self._ctx = display_context
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
                str | None,
            ]
            | None
        ) = None
        self._last_analysis_signature: tuple[str | None, str | None, str | None] | None = None
        self._last_waiting_signature: str | None = None
        # Global single-block streaming state: at most one unit has an active block.
        # _active_block maps unit_id -> (base_tag, accumulated_content).
        # Invariant: len(_active_block) <= 1.
        self._active_block: dict[str, tuple[str, list[str]]] = {}
        # Per-unit char count at the last emitted checkpoint
        self._last_checkpoint_chars: dict[str, int] = {}
        # One-shot flags for empty-state placeholders
        self._emitted_empty_plan: bool = False
        self._emitted_empty_activity: bool = False
        self._emitted_empty_decision_log: bool = False
        # Per-phase activity counters
        self._phase_counters: _PhaseCounters | None = None
        self._run_start_time: float | None = None
        self._run_counters: _PhaseCounters = _PhaseCounters()
        # Step 4: Track last emitted tool signature per unit to deduplicate META [activity] line
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
        if snapshot.phase != self._last_phase:
            self._last_phase = snapshot.phase
            self._last_iteration = snapshot.iteration
            level = LEVELS.get(snapshot.phase, "INFO")
            marker = f"{self._ctx.glyph_for('milestone')} " if level == "MILESTONE" else ""
            return [self._build_line(timestamp, level, "META", f"[phase] {marker}{snapshot.phase}")]
        if snapshot.iteration != self._last_iteration:
            self._last_iteration = snapshot.iteration
            return [
                self._build_line(
                    timestamp,
                    "INFO",
                    "META",
                    f"[progress] iteration {snapshot.iteration}/{snapshot.total_iterations}",
                )
            ]
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

        if snapshot.active_tool and snapshot.active_path:
            tool_sig = self._last_emitted_tool_signature.get(snapshot.active_unit_id or "")
            if tool_sig is not None:
                last_tool, last_path = tool_sig
                if last_tool == snapshot.active_tool and last_path == snapshot.active_path:
                    return []

        effective_activity_for_sig = structured_text

        activity_signature = (
            snapshot.active_agent,
            snapshot.active_tool,
            snapshot.active_path,
            snapshot.active_workdir,
            snapshot.active_command,
            snapshot.active_pattern,
            effective_activity_for_sig,
        )
        if activity_signature == self._last_activity_signature:
            return []
        self._last_activity_signature = activity_signature

        all_none = all(v is None for v in (
            snapshot.active_agent, snapshot.active_tool, snapshot.active_path,
            snapshot.active_workdir, snapshot.active_command, snapshot.active_pattern,
        ))
        if all_none and not snapshot.last_activity_line and not self._emitted_empty_activity:
            self._emitted_empty_activity = True
            return [self._build_line(timestamp, "INFO", "META", "[activity] (no active agent yet)")]

        if snapshot.last_activity_line:
            line_text = _sanitize(snapshot.last_activity_line)
            if snapshot.active_path:
                sanitized_path = _sanitize(snapshot.active_path)
                if sanitized_path not in line_text:
                    line_text = f"{line_text} (path={sanitized_path})"
            return [self._build_line(timestamp, "INFO", "META", f"[activity] {line_text}")]

        if activity_parts:
            return [
                self._build_line(
                    timestamp, "INFO", "META", f"[activity] {' '.join(activity_parts)}"
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

        if snapshot.analysis_phase in ("development_analysis", "review_analysis"):
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
        if snapshot.phase in ("development_analysis", "review_analysis"):
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
        if snapshot.phase == "failed" and snapshot.last_error:
            return [
                self._build_line(
                    timestamp,
                    "ERROR",
                    "META",
                    f"[failure] {_sanitize(snapshot.last_error)}",
                )
            ]
        if snapshot.phase != "complete":
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

    def emit_run_start(self, orientation: RunStartOrientation) -> None:
        """Emit a one-time MILESTONE orientation block at pipeline start."""
        timestamp = self._format_timestamp(self._clock())
        compact = self._ctx.mode == "compact"

        self._console.print(
            self._build_line(
                timestamp,
                "MILESTONE",
                "META",
                f"[run-start] {self._ctx.glyph_for('milestone')} Ralph Workflow run start",
            ),
            markup=False,
            highlight=False,
            no_wrap=True,
        )

        if orientation.legend_enabled and not compact:
            self._console.print(
                self._build_line(
                    timestamp,
                    "INFO",
                    "META",
                    "[run-start] legend: levels: INFO|SUCCESS|WARN|ERROR|MILESTONE"
                    "  cats: META|CONT  format: [tag][unit] message",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        if compact:
            self._emit_run_start_compact(timestamp, orientation)
        else:
            self._emit_run_start_wide(timestamp, orientation)

    def _emit_run_start_compact(  # noqa: PLR0912
        self, timestamp: str, orientation: RunStartOrientation
    ) -> None:
        """Compact layout: max 4 [run-start] lines (milestone + up to 3 content)."""
        # prompt+workspace combined
        prompt_ws_parts: list[str] = []
        if orientation.prompt_path is not None:
            prompt_ws_parts.append(f"prompt={_sanitize(orientation.prompt_path)}")
        if orientation.workspace_root is not None:
            prompt_ws_parts.append(f"workspace={_sanitize(orientation.workspace_root)}")
        if prompt_ws_parts:
            self._console.print(
                self._build_line(
                    timestamp, "INFO", "META", f"[run-start] {' '.join(prompt_ws_parts)}"
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        # agents+iterations combined
        agents_iters_parts: list[str] = []
        if orientation.developer_agent is not None:
            agents_iters_parts.append(f"developer={_sanitize(orientation.developer_agent)}")
        if orientation.developer_model is not None:
            agents_iters_parts.append(f"model={_sanitize(orientation.developer_model)}")
        if orientation.reviewer_agent is not None:
            agents_iters_parts.append(f"reviewer={_sanitize(orientation.reviewer_agent)}")
        if orientation.reviewer_model is not None:
            agents_iters_parts.append(f"model={_sanitize(orientation.reviewer_model)}")
        iter_compact: list[str] = []
        if orientation.developer_iters is not None:
            iter_compact.append(f"dev:{orientation.developer_iters}")
        if orientation.reviewer_reviews is not None:
            iter_compact.append(f"reviewer:{orientation.reviewer_reviews}")
        if iter_compact:
            agents_iters_parts.append(f"iterations={' '.join(iter_compact)}")
        if agents_iters_parts:
            self._console.print(
                self._build_line(
                    timestamp,
                    "INFO",
                    "META",
                    f"[run-start] {' '.join(agents_iters_parts)}",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        # plan+verbosity+parallel combined (always emitted)
        plan_val = "ready" if orientation.plan_present else "absent"
        misc_parts: list[str] = [f"plan={plan_val}"]
        if orientation.verbosity is not None:
            misc_parts.append(f"verbosity={orientation.verbosity}")
        if orientation.parallel_max_workers is not None:
            misc_parts.append(f"parallel=max_workers={orientation.parallel_max_workers}")
        self._console.print(
            self._build_line(timestamp, "INFO", "META", f"[run-start] {' '.join(misc_parts)}"),
            markup=False,
            highlight=False,
            no_wrap=True,
        )

    @staticmethod
    def _build_agents_parts(orientation: RunStartOrientation) -> list[str]:
        """Collect developer/reviewer agent+model tokens for the wide run-start agents line."""
        parts: list[str] = []
        if orientation.developer_agent is not None:
            parts.append(f"developer={_sanitize(orientation.developer_agent)}")
        if orientation.developer_model is not None:
            parts.append(f"model={_sanitize(orientation.developer_model)}")
        if orientation.reviewer_agent is not None:
            parts.append(f"reviewer={_sanitize(orientation.reviewer_agent)}")
        if orientation.reviewer_model is not None:
            parts.append(f"model={_sanitize(orientation.reviewer_model)}")
        return parts

    def _emit_run_start_wide(
        self, timestamp: str, orientation: RunStartOrientation
    ) -> None:
        """Medium/wide layout: grouped fields on shared lines."""
        # prompt+workspace on one line
        pw_parts: list[str] = []
        if orientation.prompt_path is not None:
            pw_parts.append(f"prompt={_sanitize(orientation.prompt_path)}")
        if orientation.workspace_root is not None:
            pw_parts.append(f"workspace={_sanitize(orientation.workspace_root)}")
        if pw_parts:
            self._console.print(
                self._build_line(timestamp, "INFO", "META", f"[run-start] {' '.join(pw_parts)}"),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        # agents+models on one line (developer and reviewer combined)
        agents_parts = self._build_agents_parts(orientation)
        if agents_parts:
            self._console.print(
                self._build_line(
                    timestamp, "INFO", "META", f"[run-start] {' '.join(agents_parts)}"
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        # iterations on one line
        iter_parts: list[str] = []
        if orientation.developer_iters is not None:
            iter_parts.append(f"dev:{orientation.developer_iters}")
        if orientation.reviewer_reviews is not None:
            iter_parts.append(f"reviewer:{orientation.reviewer_reviews}")
        if iter_parts:
            self._console.print(
                self._build_line(
                    timestamp,
                    "INFO",
                    "META",
                    f"[run-start] iterations={' '.join(iter_parts)}",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        if orientation.parallel_max_workers is not None:
            self._console.print(
                self._build_line(
                    timestamp,
                    "INFO",
                    "META",
                    f"[run-start] parallel=max_workers={orientation.parallel_max_workers}",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        # plan+verbosity on one line
        plan_val = "ready" if orientation.plan_present else "absent"
        plan_parts: list[str] = [f"plan={plan_val}"]
        if orientation.verbosity is not None:
            plan_parts.append(f"verbosity={orientation.verbosity}")
        self._console.print(
            self._build_line(timestamp, "INFO", "META", f"[run-start] {' '.join(plan_parts)}"),
            markup=False,
            highlight=False,
            no_wrap=True,
        )

    def begin_phase(self, phase: str) -> None:
        """Start timing a new phase and reset its counters to zero."""
        self._phase_counters = _PhaseCounters(start_time=time.monotonic())
        if self._run_start_time is None:
            self._run_start_time = time.monotonic()

    def emit_phase_close(self, phase: str, produced: str) -> None:
        """Emit a single-line recap after a phase's artifact blocks are rendered."""
        self.flush_blocks()
        timestamp = self._format_timestamp(self._clock())
        clean_produced = _sanitize(produced).strip()
        counters = self._phase_counters
        if counters is not None:
            elapsed_s = round(max(0.0, time.monotonic() - counters.start_time), 1)
        else:
            elapsed_s = 0.0
            counters = _PhaseCounters()
        suffix = (
            f" (elapsed={elapsed_s}s, content_blocks={counters.content_blocks},"
            f" thinking_blocks={counters.thinking_blocks}, tool_calls={counters.tool_calls},"
            f" errors={counters.errors})"
        )
        glyph_prefix = (
            f"{self._ctx.glyph_for('milestone')} "
            if LEVELS.get(phase) == "MILESTONE"
            else ""
        )
        if clean_produced:
            line_suffix = f"[phase-close] {glyph_prefix}phase={phase} {clean_produced}{suffix}"
        else:
            line_suffix = f"[phase-close] {glyph_prefix}phase={phase}{suffix}"
        self._console.print(
            self._build_line(timestamp, "INFO", "META", line_suffix),
            markup=False,
            highlight=False,
            no_wrap=True,
        )
        self._phase_counters = None

    def _update_counters(self, kind: str, is_new_block: bool) -> None:
        """Increment activity counters for a new streaming block.

        Run-level counters (_run_counters) are always updated for qualifying events.
        Phase-level counters (_phase_counters) are updated only when inside an active phase.
        """
        if kind == "text" and is_new_block:
            self._run_counters.content_blocks += 1
            if self._phase_counters is not None:
                self._phase_counters.content_blocks += 1
        elif kind == "thinking" and is_new_block:
            self._run_counters.thinking_blocks += 1
            if self._phase_counters is not None:
                self._phase_counters.thinking_blocks += 1
        elif kind == "tool_use":
            self._run_counters.tool_calls += 1
            if self._phase_counters is not None:
                self._phase_counters.tool_calls += 1
        elif kind == "error":
            self._run_counters.errors += 1
            if self._phase_counters is not None:
                self._phase_counters.errors += 1

    def emit_run_end(
        self,
        *,
        phase: str,
        total_agent_calls: int = 0,
        pr_url: str | None = None,
    ) -> None:
        """Emit a one-time MILESTONE orientation block at pipeline stop.

        Compact mode (<=3 lines): phase+elapsed on one line, counters on one line.
        Wide mode: one line per field with counters grouped, PR last.
        """
        self.flush_blocks()
        timestamp = self._format_timestamp(self._clock())
        total_elapsed_s = 0.0
        if self._run_start_time is not None:
            total_elapsed_s = round(max(0.0, time.monotonic() - self._run_start_time), 1)

        is_compact = self._ctx.mode == "compact"

        if is_compact:
            # Compact: 3 lines max — phase+elapsed, counters, PR
            self._console.print(
                self._build_line(
                    timestamp,
                    "MILESTONE",
                    "META",
                    f"[run-end] {phase} | {total_elapsed_s}s",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )
            # Counters on one line
            self._console.print(
                self._build_line(
                    timestamp,
                    "INFO",
                    "META",
                    f"[run-end] agent={total_agent_calls}"
                    f" content={self._run_counters.content_blocks}"
                    f" thinking={self._run_counters.thinking_blocks}"
                    f" tools={self._run_counters.tool_calls}"
                    f" errors={self._run_counters.errors}",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )
            if pr_url is not None:
                self._console.print(
                    self._build_line(
                        timestamp, "INFO", "META", f"[run-end] pr={_sanitize(pr_url)}"
                    ),
                    markup=False,
                    highlight=False,
                    no_wrap=True,
                )
        else:
            # Wide: multi-line, counters grouped, PR last
            self._console.print(
                self._build_line(
                    timestamp,
                    "MILESTONE",
                    "META",
                    f"[run-end] {self._ctx.glyph_for('milestone')} Ralph Workflow run end",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )
            self._console.print(
                self._build_line(
                    timestamp, "INFO", "META", f"[run-end] phase={phase} elapsed={total_elapsed_s}s"
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )
            # Counters grouped
            self._console.print(
                self._build_line(
                    timestamp,
                    "INFO",
                    "META",
                    f"[run-end] agent_calls={total_agent_calls}"
                    f" content_blocks={self._run_counters.content_blocks}"
                    f" thinking_blocks={self._run_counters.thinking_blocks}"
                    f" tool_calls={self._run_counters.tool_calls}"
                    f" errors={self._run_counters.errors}",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )
            if pr_url is not None:
                self._console.print(
                    self._build_line(
                        timestamp, "INFO", "META", f"[run-end] pr={_sanitize(pr_url)}"
                    ),
                    markup=False,
                    highlight=False,
                    no_wrap=True,
                )

    @property
    def content_blocks_count(self) -> int:
        return self._run_counters.content_blocks

    @property
    def thinking_blocks_count(self) -> int:
        return self._run_counters.thinking_blocks

    @property
    def tool_calls_count(self) -> int:
        return self._run_counters.tool_calls

    @property
    def errors_count(self) -> int:
        return self._run_counters.errors

    @property
    def run_elapsed_seconds(self) -> float | None:
        if self._run_start_time is None:
            return None
        return max(0.0, time.monotonic() - self._run_start_time)

    def _close_block(self, unit_id: str, timestamp: str) -> None:
        """Close an active streaming block, emitting the end-line and optional AI summary."""
        if unit_id not in self._active_block:
            return
        base_tag, accumulated = self._active_block.pop(unit_id)
        self._last_checkpoint_chars.pop(unit_id, None)
        block_tags = _STREAMING_BLOCK_TAGS.get(base_tag)
        if block_tags is None:
            return
        end_tag = block_tags[2]
        n = len(accumulated)
        chars = sum(len(x) for x in accumulated)
        joined = " ".join(accumulated)
        headline = build_headline_or_placeholder(joined, max_chars=self._ctx.headline_max_chars)
        self._console.print(
            self._build_line(
                timestamp,
                "INFO",
                "CONT",
                f"[{end_tag}][{unit_id}] ({n} fragments, {chars} chars) {headline}",
            ),
            markup=False,
            highlight=False,
            no_wrap=True,
        )

        if base_tag == "thinking":
            preview = build_headline_or_placeholder(joined, max_chars=self._ctx.headline_max_chars)
            preview_suffix = f"[{end_tag}][{unit_id}] ↳ preview: {_sanitize(preview)}"
            self._console.print(
                self._build_line(timestamp, "INFO", "CONT", preview_suffix),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        ai_summary = build_ai_summary(joined, self._ctx.env)
        if ai_summary:
            ai_text = _sanitize(ai_summary)
            self._console.print(
                self._build_line(
                    timestamp,
                    "INFO",
                    "CONT",
                    f"[{end_tag}][{unit_id}] ↳ ai-summary: {ai_text}",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

    def flush_blocks(self) -> None:
        """Close all open streaming blocks and refresh display context.

        Called on phase transitions and pipeline stop. Refreshes the
        DisplayContext to pick up any terminal resize that occurred,
        ensuring subsequent rendering uses the current width and mode.
        """
        # Refresh context to pick up new terminal size (SIGWINCH on POSIX).
        self._ctx = self._ctx.refreshed()
        timestamp = self._format_timestamp(self._clock())
        unit_ids = list(self._active_block.keys())
        for unit_id in unit_ids:
            self._close_block(unit_id, timestamp)
        self._last_emitted_tool_signature.clear()

    def emit_activity_line(  # noqa: PLR0912, PLR0913, PLR0915
        self,
        unit_id: str,
        kind: str,
        content: str,
        *,
        condensed_ref: str | None = None,
        condensed_flag: bool = False,
        summary_line: str | None = None,
        ai_summary_line: str | None = None,
        _tool_signature: tuple[str, str] | None = None,
    ) -> None:
        """Emit a kind-tagged, level-badged content line."""
        timestamp = self._format_timestamp(self._clock())
        base_tag = _KIND_TO_TAG.get(kind, "content")
        level = _KIND_TO_LEVEL.get(kind, "INFO")
        cat = _TAG_CATEGORY.get(base_tag, "META")
        sanitized = _sanitize(content)
        if condensed_ref is not None and condensed_flag:
            sanitized = f"{sanitized} [see {condensed_ref}]"

        if kind in _STREAMING_KINDS:
            if kind == "thinking" and not content.strip():
                return
            block_tags = _STREAMING_BLOCK_TAGS.get(base_tag)
            if block_tags is not None:
                start_tag, continue_tag, _end_tag = block_tags
                other_units = [uid for uid in self._active_block if uid != unit_id]
                for other_uid in other_units:
                    self._close_block(other_uid, timestamp)
                if unit_id not in self._active_block:
                    self._active_block[unit_id] = (base_tag, [content])
                    self._last_checkpoint_chars[unit_id] = 0
                    tag = start_tag
                    self._update_counters(kind, is_new_block=True)
                    if kind == "thinking":
                        headline = build_headline_or_placeholder(
                            content, max_chars=self._ctx.headline_max_chars
                        )
                        sanitized = f"↳ preview: {_sanitize(headline)}"
                else:
                    existing_base_tag, accumulated = self._active_block[unit_id]
                    if existing_base_tag != base_tag:
                        self._close_block(unit_id, timestamp)
                        self._active_block[unit_id] = (base_tag, [content])
                        self._last_checkpoint_chars[unit_id] = 0
                        tag = start_tag
                        self._update_counters(kind, is_new_block=True)
                        if kind == "thinking":
                            headline = build_headline_or_placeholder(
                                content, max_chars=self._ctx.headline_max_chars
                            )
                            sanitized = f"↳ preview: {_sanitize(headline)}"
                    else:
                        if (
                            self._ctx.streaming_dedup_enabled
                            and accumulated
                            and accumulated[-1] == content
                        ):
                            return
                        seq = len(accumulated) + 1
                        accumulated.append(content)
                        tag = f"{continue_tag}#{seq}"

                        if self._ctx.streaming_checkpoints_enabled:
                            total_chars = sum(len(x) for x in accumulated)
                            last_cp = self._last_checkpoint_chars.get(unit_id, 0)
                            emit_checkpoint = (
                                seq % self._ctx.streaming_checkpoint_fragments == 0
                                or total_chars - last_cp >= self._ctx.streaming_checkpoint_chars
                            )
                            if emit_checkpoint:
                                self._last_checkpoint_chars[unit_id] = total_chars
                                headline = build_headline_or_placeholder(
                                    " ".join(accumulated),
                                    max_chars=self._ctx.headline_max_chars,
                                )
                                cp_tag = f"{base_tag}-checkpoint#{seq}"
                                cp_suffix = (
                                    f"[{cp_tag}][{unit_id}]"
                                    f" ({seq} fragments, {total_chars} chars) {headline}"
                                )
                                self._console.print(
                                    self._build_line(
                                        timestamp, "INFO", "CONT", cp_suffix
                                    ),
                                    markup=False,
                                    highlight=False,
                                    no_wrap=True,
                                )
                                if kind == "thinking":
                                    preview = build_headline_or_placeholder(
                                        " ".join(accumulated),
                                        max_chars=self._ctx.headline_max_chars,
                                    )
                                    preview_suffix = (
                                        f"[{cp_tag}][{unit_id}] ↳ preview: "
                                        f"{_sanitize(preview)}"
                                    )
                                    self._console.print(
                                        self._build_line(
                                            timestamp, "INFO", "CONT", preview_suffix
                                        ),
                                        markup=False,
                                        highlight=False,
                                        no_wrap=True,
                                    )
                        thinking_min = self._ctx.thinking_preview_min_chars
                        if kind == "thinking" and len(content) >= thinking_min:
                            preview = build_headline_or_placeholder(
                                content, max_chars=self._ctx.headline_max_chars
                            )
                            sanitized = f"↳ preview: {_sanitize(preview)}"
            else:
                tag = base_tag
                self._update_counters(kind, is_new_block=False)

        else:
            all_units = list(self._active_block.keys())
            for uid in all_units:
                self._close_block(uid, timestamp)
            tag = base_tag
            self._update_counters(kind, is_new_block=False)

        if kind == "tool_use" and _tool_signature is not None:
            tool_name, tool_path = _tool_signature
            self._last_emitted_tool_signature[unit_id] = (tool_name, tool_path)

        if summary_line is not None:
            if summary_line:
                summary_text = _sanitize(summary_line)
                self._console.print(
                    self._build_line(
                        timestamp,
                        "INFO",
                        cat,
                        f"[{tag}][{unit_id}] ↳ summary: {summary_text}",
                    ),
                    markup=False,
                    highlight=False,
                    no_wrap=True,
                )
            elif condensed_flag:
                self._console.print(
                    self._build_line(
                        timestamp,
                        "INFO",
                        cat,
                        f"[{tag}][{unit_id}] ↳ summary: (no headline available)",
                    ),
                    markup=False,
                    highlight=False,
                    no_wrap=True,
                )

        if ai_summary_line:
            ai_text = _sanitize(ai_summary_line)
            self._console.print(
                self._build_line(
                    timestamp,
                    "INFO",
                    cat,
                    f"[{tag}][{unit_id}] ↳ ai-summary: {ai_text}",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        self._console.print(
            self._build_line(timestamp, level, cat, f"[{tag}][{unit_id}] {sanitized}"),
            markup=False,
            highlight=False,
            no_wrap=True,
        )

    def emit_log_line(self, unit_id: str, line: str) -> None:
        self.emit_activity_line(unit_id, "raw", line)

    def emit_status_line(self, unit_id: str, status: str) -> None:
        sanitized = _sanitize(status)
        self._console.out(f"[{unit_id}] status={sanitized}")

    def emit_artifact(self, kind: str, summary: str) -> None:
        """Emit an artifact summary line for copy-paste-safe transcripts."""
        timestamp = self._format_timestamp(self._clock())
        sanitized_summary = _sanitize(summary)
        self._console.print(
            self._build_line(
                timestamp,
                "INFO",
                "META",
                f"[artifact] kind={kind} summary={sanitized_summary}",
            ),
            markup=False,
            highlight=False,
            no_wrap=True,
        )

    def emit_warn_line(self, unit_id: str, tag: str, message: str) -> None:
        """Emit a WARN META line for a specific tag."""
        timestamp = self._format_timestamp(self._clock())
        cat = _TAG_CATEGORY.get(tag, "META")
        self._console.print(
            self._build_line(timestamp, "WARN", cat, f"[{tag}][{unit_id}] {message}"),
            markup=False,
            highlight=False,
            no_wrap=True,
        )


class PlainModeAdapter:
    """State subscriber that projects pipeline state to plain log lines."""

    def __init__(
        self,
        display_context: DisplayContext,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._renderer = PlainLogRenderer(display_context, clock=clock)

    def notify(self, state: PipelineState) -> None:
        self._renderer.emit_snapshot(
            snapshot_from_state(state, prompt_path=None, prompt_preview=(), run_id=None)
        )


__all__ = ["LEVELS", "_TAGS", "PlainLogRenderer", "PlainModeAdapter", "RunStartOrientation"]
