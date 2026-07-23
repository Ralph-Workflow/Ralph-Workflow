"""Parallel display adapter: always emit log-first, copy-paste-safe transcript lines.

wt-007-consolidate-display: All display logic is consolidated onto this class.
Forty-one instance methods (plus the module-level ``emit_activity_line``)
own every user-facing banner, table, panel, and status surface. Error
messages route through the existing ``emit_warning`` method with
``theme.status.error`` styling; no separate ``emit_error`` method exists.

The 41 consolidated names (run lifecycle / phase banners / artifact
renderers / tables and panels / status and warnings / first-run and
welcome / helpers):

Run lifecycle
    emit_run_start, emit_run_end, emit_parsed_event, emit_analysis_result,
    emit_completion_summary_panel

Phase banners
    emit_phase_start, emit_phase_start_from_entry, emit_phase_transition,
    emit_phase_close, emit_phase_close_from_exit, emit_phase_close_banner

Artifact renderers
    emit_plan_artifact, emit_development_artifact, emit_review_artifact,
    emit_fix_artifact, emit_analysis_decision, emit_commit_message,
    emit_missing_plan_hint

Tables and panels
    emit_agents_table, emit_providers_table, emit_config_table,
    emit_metrics_table, emit_checkpoint_summary_table,
    emit_diagnose_inventory_table, emit_diagnose_probe_table,
    emit_diagnose_servers_table, emit_capability_summary, emit_info_panel

Status and warnings
    emit_status, emit_warning, emit_skill_failure_warning,
    emit_fallback_next_steps

First-run and welcome
    emit_welcome_banner, emit_first_run_panel

Helpers
    emit_blank_line, emit_dry_run_summary

Plus the module-level ``emit_activity_line`` (1 name).

Migrated from (consolidation map)
    ralph.display.phase_banner.show_phase_start
        -> ParallelDisplay.emit_phase_start
    ralph.display.phase_banner.show_phase_start_from_entry
        -> ParallelDisplay.emit_phase_start_from_entry
    ralph.display.phase_banner.show_phase_transition
        -> ParallelDisplay.emit_phase_transition
    ralph.display.phase_banner.show_phase_close_banner
        -> ParallelDisplay.emit_phase_close_banner
    ralph.display.phase_banner.phase_style
        -> ParallelDisplay.phase_style_for_phase (public accessor)
    ralph.display.artifact_renderer.render_plan_artifact
        -> ParallelDisplay.emit_plan_artifact
    ralph.display.artifact_renderer.render_development_artifact
        -> ParallelDisplay.emit_development_artifact
    ralph.display.artifact_renderer.render_review_artifact
        -> ParallelDisplay.emit_review_artifact
    ralph.display.artifact_renderer.render_fix_artifact
        -> ParallelDisplay.emit_fix_artifact
    ralph.display.artifact_renderer.render_analysis_decision
        -> ParallelDisplay.emit_analysis_decision
    ralph.display.artifact_renderer.render_commit_message
        -> ParallelDisplay.emit_commit_message
    ralph.display.artifact_renderer.render_missing_plan_hint
        -> ParallelDisplay.emit_missing_plan_hint
    ralph.display.first_run_panel.render_first_run_panel
        -> ParallelDisplay.emit_first_run_panel
    ralph.display.tables.show_metrics
        -> ParallelDisplay.emit_metrics_table
    ralph.display.tables.show_checkpoint_summary
        -> ParallelDisplay.emit_checkpoint_summary_table
    ralph.display.tables.show_agents
        -> ParallelDisplay.emit_agents_table
    ralph.display.tables.show_providers
        -> ParallelDisplay.emit_providers_table
    ralph.display.tables.show_config
        -> ParallelDisplay.emit_config_table
    ralph.banner.show_banner
        -> ParallelDisplay.emit_welcome_banner
    ralph.cli.options.display_agents_table
        -> ParallelDisplay.emit_agents_table
    ralph.cli.options.display_providers_table
        -> ParallelDisplay.emit_providers_table
    ralph.display.plain_renderer.PlainLogRenderer
        -> ParallelDisplay (inlined as private methods and instance state)
"""

from __future__ import annotations

import contextlib
import json
import queue
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.text import Text as _RichText

from ralph.display._activity_line_options import ActivityLineOptions as _ActivityLineOptions
from ralph.display._phase_close_counters import _PhaseCloseCounters
from ralph.display._phase_close_options import PhaseCloseOptions
from ralph.display._phase_counters import PhaseCounters as _PhaseCounters
from ralph.display._plain_constants import (
    _CAT_THEME_KEYS,
    _EMPTY_PLAN_SIGNATURE,
    _KIND_TO_LEVEL,
    _KIND_TO_TAG,
    _LEVEL_THEME_KEYS,
    _STREAMING_BLOCK_TAGS,
    _STREAMING_KINDS,
    LEVELS,
    TAG_CATEGORY,
    _sanitize,
)
from ralph.display._streaming_ctx import _StreamingCtx
from ralph.display.activity_model import ActivityEventKind
from ralph.display.activity_router import ActivityRouter
from ralph.display.agent_event_renderer import render_event_kind_text
from ralph.display.artifact_reader import (
    read_latest_analysis_decision,
    read_plan_artifact,
)
from ralph.display.content_condenser import CondenseOptions, condense_content
from ralph.display.context import DisplayContext
from ralph.display.lifecycle_filter import is_bare_lifecycle as _is_bare_lifecycle
from ralph.display.line_sanitizer import strip_terminal_control
from ralph.display.long_content_summary import (
    build_ai_summary,
    build_headline_or_placeholder,
)
from ralph.display.phase_status import (
    format_analysis_cycle,
    format_dev_cycle,
    format_elapsed_seconds,
    format_transition_context_items,
)
from ralph.display.raw_overflow import DEFAULT_MAX_OVERFLOW_FILE_BYTES, RawOverflowLog
from ralph.display.subscriber import PipelineSubscriber
from ralph.mcp.artifacts.commit_message import read_commit_message_artifact
from ralph.mcp.artifacts.handoffs import (
    ensure_markdown_handoff_from_artifact,
    handoff_path_for_artifact,
)

if TYPE_CHECKING:
    from types import TracebackType

    from rich.console import Console, RenderableType

    from ralph.config.models import UnifiedConfig
    from ralph.display._run_start_orientation import RunStartOrientation
    from ralph.display.completion_summary import CompletionSummaryOptions
    from ralph.display.phase_lifecycle import PhaseEntryModel, PhaseExitModel
    from ralph.display.phase_status import PhaseIterationContext
    from ralph.display.snapshot import PipelineSnapshot
    from ralph.pipeline.worker_state import WorkerStatus
    from ralph.policy.models import PipelinePolicy
    from ralph.skills._capability_state import CapabilityState

_DEFAULT_SNAPSHOT_QUEUE_MAXSIZE: int = 64
_MAX_OVERFLOW_FILE_BYTES: int = DEFAULT_MAX_OVERFLOW_FILE_BYTES
_DROP_DEBOUNCE_SECONDS: float = 1.0
_NEVER_WARNED: float = float("-inf")
_MAX_RENDERED_UNIT_ID_CHARS = 24
_MAX_STREAMING_FRAGMENTS: int = 2048

# A tool activity is "repeated" (coalesced with a "xN" count in the live status)
# starting from the second consecutive identical call.
_MIN_COALESCE_REPEAT = 2


def _strip_control_chars_for_render(text: str) -> str:
    """Strip control characters and ANSI escape sequences that could break the transcript.

    Display-bound text is rendered into the live transcript with a fixed
    badge contract (``[LEVEL] [CAT] [tag][unit_id] body``). A newline in
    ``unit_id`` or in ``message`` would split the rendered line and let
    the next fragment hide under the wrong badge; a raw control sequence
    could also inject into the user's scrollback. This helper collapses
    CRLF to LF, then removes every control character and ANSI escape
    (the same contract as :func:`_sanitize`, but applied to text that is
    NOT expected to contain legitimate markup, so it is safe to also
    strip embedded newlines and tabs).

    Args:
        text: Arbitrary user-controlled string destined for a transcript
            line. May contain ``\\n`` / ``\\r`` / ``\\x1b`` / ``\\x00`` etc.

    Returns:
        A safe string with no embedded newlines, tabs, or control
        sequences. The visible content is preserved so the user still
        sees the meaningful payload.
    """
    return _sanitize(text).replace("\n", " ").replace("\t", " ")


def _render_unit_id(unit_id: str) -> str:
    """Bound visible unit ids so prefixes cannot hide the activity payload.

    Display-bound ``unit_id`` strings are sanitized first: embedded
    newlines, tabs, ANSI escapes, and other control characters are
    removed or replaced with spaces so a malicious or malformed unit id
    cannot break the transcript line layout or inject control sequences
    into the user's scrollback.
    """
    sanitized = _strip_control_chars_for_render(unit_id)
    if len(sanitized) <= _MAX_RENDERED_UNIT_ID_CHARS:
        return sanitized
    return f"{sanitized[: _MAX_RENDERED_UNIT_ID_CHARS - 3]}..."


# ASCII banner art inlined from the deleted ralph.banner module so
# emit_welcome_banner does not need a separate module-level import for
# these constants.
_ASCII_ART_BANNER: tuple[str, ...] = (
    " ____       _       _     _     ",
    "|  _ \\ __ _| |_ __ | |__ | |__  ",
    "| |_) / _` | | '_ \\| '_ \\| '_ \\ ",
    "|  _ < (_| | | |_) | | | | | | |",
    "|_| \\_\\__,_|_| .__/|_| |_|_| |_|",
    "              |_|                ",
)
_WELCOME_MESSAGE_TEXT: str = "Welcome to Ralph Workflow"
_TAGLINE_TEXT: str = "PROMPT-driven agent orchestrator"

# Phase banner helpers (port of phase_banner.py). These are private to
# parallel_display and the I/O bodies (show_*_phase_*) have been moved onto
# ParallelDisplay. The pure helpers phase_style, phase_label, _PHASE_STYLES,
# MAJOR_ROLE_PAIRS, _resolve_transition_meta, _build_outer_iteration_suffix,
# _build_inner_analysis_suffix stay here so the new emit_* methods have
# non-I/O pure logic to call. Tests that previously imported from
# ralph.display.phase_banner now import from ralph.display.parallel_display
# (or, in the future, from ralph.display._phase_banner).
_PHASE_STYLES: dict[str, str] = {
    "execution": "theme.phase.development",
    "analysis": "theme.phase.development_analysis",
    "review": "theme.phase.review",
    "commit": "theme.phase.commit",
    "fix": "theme.phase.fix",
    "verification": "theme.phase.development_analysis",
    "terminal": "theme.phase.complete",
    "fanout_join": "theme.phase.development",
    # Not declared in pipeline.toml, and deliberately so: rebase conflict
    # resolution is not a pipeline phase but a nested resolution pipeline
    # entered from the auto-integration seams. It therefore never resolves
    # through the pipeline-policy branch below, and without this entry
    # _phase_style falls through to "theme.text.muted" -- rendering the one
    # phase the operator most needs to see as inert grey text. The name is
    # a literal rather than an import of PHASE_RESOLUTION
    # (ralph/pipeline/conflict_resolution/graph.py) because ralph.display
    # must not import ralph.pipeline at runtime.
    "rebase_conflict_resolution": "theme.phase.fix",
}

_MAJOR_ROLE_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {
        ("execution", "analysis"),
        ("analysis", "commit"),
        ("commit", "review"),
        ("review", "analysis"),
        ("analysis", "execution"),
        ("commit", "execution"),
        ("commit", "terminal"),
        ("review", "terminal"),
        ("execution", "terminal"),
    }
)


# Public alias for tests that previously imported ``MAJOR_ROLE_PAIRS`` from
# ``ralph.display.phase_banner``. Keeps the consolidated surface reachable
# while keeping the private underscore-prefixed implementation detail.
MAJOR_ROLE_PAIRS = _MAJOR_ROLE_PAIRS

# Column counts for the diagnose tables. Kept at module scope so PLR2004
# (magic-value comparison) does not fire on the cell-padding loops.
_INVENTORY_TABLE_COLUMNS = 4
_PROBE_TABLE_COLUMNS = 5
_SERVERS_TABLE_COLUMNS = 5


def _phase_style(phase: str, pipeline_policy: PipelinePolicy | None = None) -> str:
    """Pure helper: return the rich style string for a phase name or role."""
    if pipeline_policy is not None:
        phase_def = pipeline_policy.phases.get(phase)
        if phase_def is not None:
            if phase_def.display_style is not None:
                return phase_def.display_style
            role = phase_def.role or ""
            terminal_outcome = phase_def.terminal_outcome
            if role == "terminal" and terminal_outcome == "failure":
                return "theme.phase.failed"
            style = _PHASE_STYLES.get(role)
            if style is not None:
                return style
    return _PHASE_STYLES.get(phase, "theme.text.muted")


def _phase_label(phase: str) -> str:
    """Pure helper: return a human-readable label for a phase name."""
    return phase.replace("_", " ").title()


# Public aliases for tests and other callers that previously imported
# ``phase_style`` / ``phase_label`` from ``ralph.display.phase_banner``.
phase_style = _phase_style
phase_label = _phase_label


def phase_style_for_phase(
    phase: str,
    pipeline_policy: PipelinePolicy | None = None,
) -> str:
    """Public accessor that exposes the private ``_phase_style`` helper.

    Callers that previously imported ``phase_style`` from
    ``ralph.display.phase_banner`` should import this accessor instead
    so they can route through ParallelDisplay's consolidated surface.
    """
    return _phase_style(phase, pipeline_policy)


def _resolve_transition_meta(
    from_phase: str,
    to_phase: str,
    pipeline_policy: PipelinePolicy | None,
) -> bool:
    """Pure helper: return is_major for a phase transition."""
    if pipeline_policy is None:
        return False
    phases = pipeline_policy.phases
    from_def = phases.get(from_phase)
    to_def = phases.get(to_phase)
    if from_def is None or to_def is None:
        return False
    from_role = from_def.role or ""
    to_role = to_def.role or ""
    return (from_role, to_role) in _MAJOR_ROLE_PAIRS


def _build_outer_iteration_suffix(
    iteration: int | None,
    cap: int | None = None,
    *,
    od_glyph: str = "\u229e",
    qualifier: str = "",
) -> str:
    if iteration is None:
        return ""
    qual = f" {qualifier}" if qualifier else ""
    return f"  {od_glyph} {format_dev_cycle(iteration, cap)}{qual}"


def _build_inner_analysis_suffix(
    inner: int | None,
    max_inner: int | None = None,
    *,
    ia_glyph: str = "\u2274",
    qualifier: str = "",
) -> str:
    if inner is None:
        return ""
    qual = f" {qualifier}" if qualifier else ""
    return f"  {ia_glyph} {format_analysis_cycle(inner, max_inner)}{qual}"


_ARTIFACTS_DIR: str = ".agent/artifacts"


def strip_markup(line: str) -> str:
    """Strip Rich markup tags from a line, returning plain text."""
    return ParallelDisplay.strip_markup(line)


class ParallelDisplay:
    """Multiplexed terminal display for parallel pipeline workers.

    Maintains per-worker ``RingBuffer`` instances through an ``ActivityRouter``
    and renders them as a live Rich table while agents are running.

    All display logic lives on this class; the previously separate
    ``PlainLogRenderer`` in ``ralph.display.plain_renderer`` has been
    inlined as private methods and instance state. The 22 state
    attributes that used to live on ``_PlainLogRendererBase`` (run
    counters, phase counters, active streaming block map, last-emitted
    tool signatures, last-broadcast signature caches) are documented in
    ``__slots__`` so the existing ``__slots__`` discipline is preserved.
    """

    __slots__ = (
        "_active_block",
        "_active_block_chars",
        "_activity_router",
        "_clock",
        "_ctx",
        "_drop_last_warned",
        "_emitted_empty_activity",
        "_emitted_empty_decision_log",
        "_emitted_empty_plan",
        "_is_quiet",
        "_last_activity_signature",
        "_last_analysis_signature",
        "_last_budget_progress",
        "_last_checkpoint_chars",
        "_last_emitted_tool_signature",
        "_last_phase",
        "_last_phase_artifact_outcome",
        "_last_phase_elapsed_seconds",
        "_last_phase_saved_counters",
        "_last_plan_signature",
        "_last_waiting_signature",
        "_last_worker_states",
        "_monotonic",
        "_overflow_logs",
        "_overflow_warned",
        "_phase_close_emitted",
        "_phase_counters",
        "_run_counters",
        "_run_start_time",
        "_status_bar",
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
        is_quiet: bool = False,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        # Re-validate at runtime: a duck-typed stand-in (e.g. test stub) is
        # permitted provided it exposes ``.console``. The strict type contract
        # is preserved for production callers; the runtime check below is the
        # only point that tolerates test stand-ins.
        if not hasattr(display_context, "console"):
            raise TypeError("display_context is required")
        self._ctx = display_context
        self._is_quiet: bool = is_quiet
        self._clock: Callable[[], datetime] = (
            clock if clock is not None else (lambda: datetime.now(UTC))
        )
        self._monotonic: Callable[[], float] = (
            monotonic if monotonic is not None else time.monotonic
        )

        # Inlined from _PlainLogRendererBase.__init__ -- 22 state attributes
        # that previously lived on a separate renderer instance. Documented in
        # __slots__ above so the existing __slots__ discipline is preserved.
        self._last_phase: str | None = None
        # phase-bounded: replaced wholesale each snapshot (NOT per-unit accumulated)
        self._last_budget_progress: dict[str, int] = {}  # bounded-accumulator-ok: phase-bounded
        # per-unit; drained by drop_unit(unit_id) in the parallel coordinator finally
        self._last_worker_states: dict[str, str] = {}  # bounded-accumulator-ok: drop_unit
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
                str | None,
                int,
            ]
            | None
        ) = None
        self._last_analysis_signature: tuple[str | None, str | None, str | None] | None = None
        self._last_waiting_signature: str | None = None
        # per-unit; drained by drop_unit(unit_id) in the parallel coordinator finally
        self._active_block: dict[str, tuple[str, list[str]]] = {}  # bounded-accumulator-ok
        self._active_block_chars: dict[str, int] = {}  # bounded-accumulator-ok: drop_unit
        self._last_checkpoint_chars: dict[str, int] = {}  # bounded-accumulator-ok: drop_unit
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
        # per-unit; drained by drop_unit(unit_id) in the parallel coordinator finally
        self._last_emitted_tool_signature: dict[str, tuple[str, str]] = {}  # bounded-accumulator-ok

        self._workspace_root: Path = workspace_root if workspace_root is not None else Path.cwd()

        # Per-unit raw overflow logs, lazy-created on first oversized emit
        # Per-unit raw overflow logs, lazy-created on first oversized emit
        # per-unit; drained by drop_unit(unit_id) in the parallel coordinator finally
        self._overflow_logs: dict[str, RawOverflowLog] = {}  # bounded-accumulator-ok: drop_unit
        # Track units where the 50 MB guard WARN was already emitted
        self._overflow_warned: set[str] = set()  # bounded-accumulator-ok: drop_unit
        # Per-unit last drop-warning timestamp; _NEVER_WARNED means never warned yet
        self._drop_last_warned: dict[str, float] = {}  # bounded-accumulator-ok: drop_unit

        self._activity_router: ActivityRouter = ActivityRouter(
            on_event=self._emit_activity_event,
            raw_overflow_callback=self._raw_overflow_write,
        )

        # Persistent bottom Status Bar — composed owner for run-level layout,
        # color, spacing, truncation, and live-update behavior. The canonical
        # emit_* set (see ``_PARALLEL_DISPLAY_ALL_NAMES`` in the drift-prevention
        # test) is the single source of truth for the one-shot surface; the
        # StatusBar is the single owner of the persistent footer lifecycle.
        from ralph.display.status_bar import StatusBar

        self._status_bar: StatusBar = StatusBar(self)

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
                on_snapshot=self.emit_snapshot,
                pipeline_policy=pipeline_policy,
            )

    @property
    def _console(self) -> Console:
        return self._ctx.console

    # -- Pure helpers (inlined from _PlainLogRendererBase) ----------------

    def _format_timestamp(self, ts: datetime) -> str:
        """Format a datetime as an ISO 8601 timestamp string (single default mode)."""
        return ts.isoformat()

    def _build_line(self, timestamp: str, level: str, cat: str, suffix: str) -> Text:
        """Build a styled Text line with level and category badge segments."""
        t = Text()
        t.append(timestamp + " ")
        t.append(level, style=_LEVEL_THEME_KEYS.get(level, ""))
        t.append(" ")
        t.append(cat, style=_CAT_THEME_KEYS.get(cat, ""))
        t.append(" ")
        t.append(suffix)
        return t

    @staticmethod
    def _build_agents_parts(orientation: RunStartOrientation) -> list[str]:
        """Collect developer agent+model tokens for the run-start agents line."""
        parts: list[str] = []
        if orientation.developer_agent is not None:
            parts.append(f"developer={_sanitize(orientation.developer_agent)}")
        if orientation.developer_model is not None:
            parts.append(f"model={_sanitize(orientation.developer_model)}")
        return parts

    @classmethod
    def strip_markup(cls, line: str) -> str:
        """Strip terminal control sequences from a line, returning plain text.

        Delegates the escape strip to :func:`strip_terminal_control` so every
        CSI / OSC / C0 sequence (alternate screen, erase display, private
        parameter forms like ``ESC[>0c`` and ``ESC[<35;1;2M``, OSC titles) is
        removed. After the wt-028-display consolidation the helper no
        longer strips Rich markup because every consumer in this module
        prints the result through a Console with ``markup=False``; a
        Rich ``[red]...[/red]`` style therefore cannot reach the
        terminal and stripping it would mutate literal agent content
        (``[result] ok`` -> ``ok``).
        """
        return strip_terminal_control(line)

    # -- Structured log emit (inlined from PlainLogRenderer) ---------------

    def emit_activity_line(
        self,
        unit_id: str,
        kind: str,
        content: str,
        *,
        options: _ActivityLineOptions | None = None,
        condensed_ref: str | None = None,
        condensed_flag: bool = False,
        summary_line: str | None = None,
        ai_summary_line: str | None = None,
        tool_signature: tuple[str, str] | None = None,
    ) -> None:
        """Emit a kind-tagged, level-badged content line."""
        if options is None:
            options = _ActivityLineOptions(
                condensed_ref=condensed_ref,
                condensed_flag=condensed_flag,
                summary_line=summary_line,
                ai_summary_line=ai_summary_line,
                tool_signature=tool_signature,
            )
        opts = options
        timestamp = self._format_timestamp(self._clock())
        rendered_unit_id = _render_unit_id(unit_id)
        base_tag = _KIND_TO_TAG.get(kind, "content")
        level = _KIND_TO_LEVEL.get(kind, "INFO")
        cat = TAG_CATEGORY.get(base_tag, "META")
        sanitized = _sanitize(content)
        if opts.condensed_ref is not None and opts.condensed_flag:
            sanitized = f"{sanitized} [see {opts.condensed_ref}]"

        if kind in _STREAMING_KINDS:
            if kind == "thinking" and not content.strip():
                return
            block_tags = _STREAMING_BLOCK_TAGS.get(base_tag)
            if block_tags is not None:
                ctx = _StreamingCtx(
                    unit_id=unit_id,
                    kind=kind,
                    content=content,
                    base_tag=base_tag,
                    timestamp=timestamp,
                )
                result = self._process_streaming_block(ctx, block_tags)
                if result is None:
                    return
                tag, sanitized_override = result
                if sanitized_override is not None:
                    sanitized = sanitized_override
            else:
                tag = base_tag
                self._update_counters(kind, is_new_block=False)
        else:
            for uid in list(self._active_block.keys()):
                self._close_block(uid, timestamp)
            tag = base_tag
            self._update_counters(kind, is_new_block=False)

        if kind == "tool_use" and opts.tool_signature is not None:
            tool_name, tool_path = opts.tool_signature
            self._last_emitted_tool_signature[unit_id] = (tool_name, tool_path)

        self._emit_activity_supplements(unit_id, timestamp, tag, cat, opts)

        self._console.print(
            self._build_line(timestamp, level, cat, f"[{tag}][{rendered_unit_id}] {sanitized}"),
            markup=False,
            highlight=False,
            no_wrap=False,
            overflow="fold",
        )

    def emit_log_line(self, unit_id: str, line: str) -> None:
        """Emit a per-unit raw-log line routed through emit_activity_line with kind=raw.

        The line is sanitized, timestamped with the configured clock, and
        rendered with the standard INFO/META badge contract. No-op when
        ``is_quiet`` is true so machine-friendly runs stay clean.
        """
        if self._is_quiet:
            return
        self.emit_activity_line(unit_id, "raw", line)

    def emit_status_line(self, unit_id: str, status: str) -> None:
        """Emit a status line with the same TIMESTAMP LEVEL CAT badge as other lines.

        No-op when ``is_quiet`` is true; quiet-mode machine-friendly runs
        must not surface per-unit status banners.
        """
        if self._is_quiet:
            return
        timestamp = self._format_timestamp(self._clock())
        sanitized = _sanitize(status)
        rendered_unit_id = _render_unit_id(unit_id)
        self._console.print(
            self._build_line(
                timestamp,
                "INFO",
                "META",
                f"[status][{rendered_unit_id}] {sanitized}",
            ),
            markup=False,
            highlight=False,
            no_wrap=False,
            overflow="fold",
        )

    def emit_warn_line(self, unit_id: str, tag: str, message: str) -> None:
        """Emit a WARN META line for a specific tag.

        Both ``tag`` and ``message`` are display-bound user-controlled
        strings. They are sanitized for control characters, embedded
        newlines, and ANSI escapes before being interpolated into the
        fixed-format line so a malformed or hostile caller cannot break
        the transcript line layout or inject control sequences into the
        user's scrollback.
        """
        timestamp = self._format_timestamp(self._clock())
        cat = TAG_CATEGORY.get(tag, "META")
        rendered_unit_id = _render_unit_id(unit_id)
        sanitized_tag = _strip_control_chars_for_render(tag)
        sanitized_message = _strip_control_chars_for_render(message)
        self._console.print(
            self._build_line(
                timestamp,
                "WARN",
                cat,
                f"[{sanitized_tag}][{rendered_unit_id}] {sanitized_message}",
            ),
            markup=False,
            highlight=False,
            no_wrap=False,
            overflow="fold",
        )

    # -- Streaming block helpers (inlined from PlainLogRenderer) -----------

    def _update_counters(self, kind: str, is_new_block: bool) -> None:
        """Increment activity counters for a new streaming block."""
        if kind == "text" and is_new_block:
            self._run_counters.content_blocks += 1
            if self._phase_counters is not None:
                self._phase_counters.content_blocks += 1
        elif kind == "thinking" and is_new_block:
            self._run_counters.thinking_blocks += 1
            if self._phase_counters is not None:
                self._phase_counters.thinking_blocks += 1
        elif kind in {"tool_use", "tool_result"}:
            self._run_counters.tool_calls += 1
            if self._phase_counters is not None:
                self._phase_counters.tool_calls += 1
        elif kind == "error":
            self._run_counters.errors += 1
            if self._phase_counters is not None:
                self._phase_counters.errors += 1

    def _emit_activity_supplements(
        self,
        unit_id: str,
        timestamp: str,
        tag: str,
        cat: str,
        opts: _ActivityLineOptions,
    ) -> None:
        """Emit optional summary and ai-summary lines before the main activity line."""
        rendered_unit_id = _render_unit_id(unit_id)
        if opts.summary_line is not None:
            if opts.summary_line:
                summary_text = _sanitize(opts.summary_line)
                self._console.print(
                    self._build_line(
                        timestamp,
                        "INFO",
                        cat,
                        f"[{tag}][{rendered_unit_id}] \u21b3 summary: {summary_text}",
                    ),
                    markup=False,
                    highlight=False,
                    no_wrap=False,
                    overflow="fold",
                )
            elif opts.condensed_flag:
                self._console.print(
                    self._build_line(
                        timestamp,
                        "INFO",
                        cat,
                        f"[{tag}][{rendered_unit_id}] \u21b3 summary: (no headline available)",
                    ),
                    markup=False,
                    highlight=False,
                    no_wrap=False,
                    overflow="fold",
                )
        if opts.ai_summary_line:
            ai_text = _sanitize(opts.ai_summary_line)
            self._console.print(
                self._build_line(
                    timestamp,
                    "INFO",
                    cat,
                    f"[{tag}][{rendered_unit_id}] \u21b3 ai-summary: {ai_text}",
                ),
                markup=False,
                highlight=False,
                no_wrap=False,
                overflow="fold",
            )

    def _close_block(self, unit_id: str, timestamp: str) -> None:
        """Close an active streaming block, emitting the end-line and optional AI summary."""
        if unit_id not in self._active_block:
            return
        rendered_unit_id = _render_unit_id(unit_id)
        base_tag, accumulated = self._active_block.pop(unit_id)
        self._last_checkpoint_chars.pop(unit_id, None)
        self._active_block_chars.pop(unit_id, None)
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
                f"[{end_tag}][{rendered_unit_id}] ({n} fragments, {chars} chars) {headline}",
            ),
            markup=False,
            highlight=False,
            no_wrap=False,
            overflow="fold",
        )

        if base_tag == "thinking":
            preview = build_headline_or_placeholder(joined, max_chars=self._ctx.headline_max_chars)
            preview_suffix = f"[{end_tag}][{rendered_unit_id}] \u21b3 preview: {_sanitize(preview)}"
            self._console.print(
                self._build_line(timestamp, "INFO", "CONT", preview_suffix),
                markup=False,
                highlight=False,
                no_wrap=False,
                overflow="fold",
            )

        ai_summary = build_ai_summary(joined, self._ctx.env)
        if ai_summary:
            ai_text = _sanitize(ai_summary)
            self._console.print(
                self._build_line(
                    timestamp,
                    "INFO",
                    "CONT",
                    f"[{end_tag}][{rendered_unit_id}] \u21b3 ai-summary: {ai_text}",
                ),
                markup=False,
                highlight=False,
                no_wrap=False,
                overflow="fold",
            )

    def flush_blocks(self) -> None:
        """Close all open streaming blocks and refresh display context."""
        self._ctx = self._ctx.refreshed()
        timestamp = self._format_timestamp(self._clock())
        unit_ids = list(self._active_block.keys())
        for unit_id in unit_ids:
            self._close_block(unit_id, timestamp)
        self._last_emitted_tool_signature.clear()

    def _handle_new_streaming_block(
        self,
        ctx: _StreamingCtx,
        start_tag: str,
    ) -> tuple[str, str | None]:
        """Open a new streaming block and return (tag, sanitized_override | None)."""
        self._active_block[ctx.unit_id] = (ctx.base_tag, [ctx.content])
        self._last_checkpoint_chars[ctx.unit_id] = 0
        self._active_block_chars[ctx.unit_id] = len(ctx.content)
        self._update_counters(ctx.kind, is_new_block=True)
        if ctx.kind == "thinking":
            headline = build_headline_or_placeholder(
                ctx.content, max_chars=self._ctx.headline_max_chars
            )
            return start_tag, f"\u21b3 preview: {_sanitize(headline)}"
        return start_tag, None

    def _continue_streaming_block(
        self,
        ctx: _StreamingCtx,
        accumulated: list[str],
        continue_tag: str,
        start_tag: str,
    ) -> tuple[str, str | None] | None:
        """Continue an existing streaming block; returns (tag, override) or None for dedup."""
        if self._ctx.streaming_dedup_enabled and accumulated and accumulated[-1] == ctx.content:
            return None
        if len(accumulated) >= _MAX_STREAMING_FRAGMENTS:
            self._close_block(ctx.unit_id, ctx.timestamp)
            return self._handle_new_streaming_block(ctx, start_tag)
        seq = len(accumulated) + 1
        accumulated.append(ctx.content)
        running_total = self._active_block_chars.get(ctx.unit_id, 0) + len(ctx.content)
        self._active_block_chars[ctx.unit_id] = running_total
        tag = f"{continue_tag}#{seq}"
        if self._ctx.streaming_checkpoints_enabled:
            total_chars = self._active_block_chars.get(ctx.unit_id, 0)
            last_cp = self._last_checkpoint_chars.get(ctx.unit_id, 0)
            emit_checkpoint = (
                seq % self._ctx.streaming_checkpoint_fragments == 0
                or total_chars - last_cp >= self._ctx.streaming_checkpoint_chars
            )
            if emit_checkpoint:
                self._last_checkpoint_chars[ctx.unit_id] = total_chars
                rendered_unit_id = _render_unit_id(ctx.unit_id)
                headline = build_headline_or_placeholder(
                    " ".join(accumulated), max_chars=self._ctx.headline_max_chars
                )
                cp_tag = f"{ctx.base_tag}-checkpoint#{seq}"
                cp_suffix = (
                    f"[{cp_tag}][{rendered_unit_id}] "
                    f"({seq} fragments, {total_chars} chars) {headline}"
                )
                self._console.print(
                    self._build_line(ctx.timestamp, "INFO", "CONT", cp_suffix),
                    markup=False,
                    highlight=False,
                    no_wrap=False,
                    overflow="fold",
                )
                if ctx.kind == "thinking":
                    preview = build_headline_or_placeholder(
                        " ".join(accumulated), max_chars=self._ctx.headline_max_chars
                    )
                    preview_suffix = (
                        f"[{cp_tag}][{rendered_unit_id}] \u21b3 preview: {_sanitize(preview)}"
                    )
                    self._console.print(
                        self._build_line(ctx.timestamp, "INFO", "CONT", preview_suffix),
                        markup=False,
                        highlight=False,
                        no_wrap=False,
                        overflow="fold",
                    )
        thinking_min = self._ctx.thinking_preview_min_chars
        if ctx.kind == "thinking" and len(ctx.content) >= thinking_min:
            preview = build_headline_or_placeholder(
                ctx.content, max_chars=self._ctx.headline_max_chars
            )
            return tag, f"\u21b3 preview: {_sanitize(preview)}"
        return tag, None

    def _process_streaming_block(
        self,
        ctx: _StreamingCtx,
        block_tags: tuple[str, str, str],
    ) -> tuple[str, str | None] | None:
        """Dispatch streaming block state; returns (tag, override) or None on dedup/early-return."""
        start_tag, continue_tag, _end_tag = block_tags
        for other_uid in [uid for uid in self._active_block if uid != ctx.unit_id]:
            self._close_block(other_uid, ctx.timestamp)
        if ctx.unit_id not in self._active_block:
            return self._handle_new_streaming_block(ctx, start_tag)
        existing_base_tag, accumulated = self._active_block[ctx.unit_id]
        if existing_base_tag != ctx.base_tag:
            self._close_block(ctx.unit_id, ctx.timestamp)
            return self._handle_new_streaming_block(ctx, start_tag)
        return self._continue_streaming_block(ctx, accumulated, continue_tag, start_tag)

    # -- Snapshot / view (inlined from _PlainLogRendererBase) --------------

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
                    f"[plan-steps] "
                    f"{snapshot.plan_current_step or '\u2014'}/{snapshot.plan_total_steps}",
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

        repeat = snapshot.active_tool_repeat
        is_repeat = repeat >= _MIN_COALESCE_REPEAT

        if not is_repeat and snapshot.active_tool and snapshot.active_path:
            tool_sig = self._last_emitted_tool_signature.get(snapshot.active_unit_id or "")
            if tool_sig is not None:
                last_tool, last_path = tool_sig
                if last_tool == snapshot.active_tool and last_path == snapshot.active_path:
                    return []

        activity_signature = (
            snapshot.active_agent,
            snapshot.active_tool,
            snapshot.active_path,
            snapshot.active_workdir,
            snapshot.active_command,
            snapshot.active_pattern,
            snapshot.last_activity_line
            if snapshot.active_tool is None and snapshot.active_path is None
            else None,
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

        reason = (
            f" \u2014 {_sanitize(snapshot.analysis_reason)}" if snapshot.analysis_reason else ""
        )
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

    def emit_snapshot(self, snapshot: PipelineSnapshot) -> None:
        """Sink for PipelineSubscriber snapshot events.

        The constructor wires on_snapshot=self.emit_snapshot. A snapshot
        becomes a series of INFO/META lines tagged with the snapshot's
        unit_id and the originating worker's metadata.
        """
        for text in self._snapshot_texts(snapshot):
            self._console.print(text, markup=False, highlight=False, no_wrap=True)

    @property
    def _plain_renderer(self) -> ParallelDisplay:
        """Compatibility view for callers that predate renderer inlining."""
        return self

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
        return max(0.0, self._monotonic() - self._run_start_time)

    def _get_overflow_log(self, unit_id: str) -> RawOverflowLog:
        if unit_id not in self._overflow_logs:
            self._overflow_logs[unit_id] = RawOverflowLog(
                self._workspace_root, unit_id, max_bytes=_MAX_OVERFLOW_FILE_BYTES
            )
        return self._overflow_logs[unit_id]

    def _raw_overflow_write(self, unit_id: str, raw_line: str) -> None:
        """Write a raw malformed line to the per-unit overflow log for diagnosis.

        Routes through ``_check_overflow_size`` so the parser-failure
        path inherits the same size-guard + one-shot warning logic
        the condensed-content path uses. Without this, raw overflow
        could silently hit the 50 MB cap and disable without
        surfacing the ``[overflow log full ...]`` warning the
        operator relies on.
        """
        overflow = self._get_overflow_log(unit_id)
        overflow.append(raw_line)
        self._check_overflow_size(unit_id, overflow)

    def _check_overflow_size(self, unit_id: str, overflow: RawOverflowLog) -> None:
        """Emit a single WARN and disable the log if it exceeds the size guard.

        Uses the in-memory ``size_bytes`` counter (NOT a
        ``path.stat().st_size`` probe) so the size guard is
        flush-independent: the warning fires on the first append
        that crosses the cap rather than waiting for the next 5 s
        flush to catch up. Also covers the ``is_disabled`` branch
        where ``append()`` auto-disabled the log when the byte cap
        was reached mid-write — ``size_bytes`` is authoritative in
        that case, and the warning still has to surface so the
        operator learns the cap was hit.
        """
        if unit_id in self._overflow_warned:
            return
        if overflow.size_bytes >= _MAX_OVERFLOW_FILE_BYTES or overflow.is_disabled:
            self._overflow_warned.add(unit_id)
            overflow.disable()
            self.emit_activity_line(
                unit_id,
                "progress",
                f"\\[overflow log full, raw content for {unit_id} discarded]",
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
        self.emit_warn_line(
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
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Render an agent event through the single registry and emit it.

        After the wt-028-display consolidation, every agent-event
        formatting decision lives in
        :mod:`ralph.display.agent_event_renderer`. This function owns
        the *delivery* of the event (overflow tracking, badge
        wrapping, drop-warning, subscriber metadata): it routes the
        presentation through :func:`agent_event_renderer.render_event_kind_text`
        and forwards the visible text into ``emit_activity_line`` so
        the standard timestamp + level + cat badge contract is
        preserved. The same registry powers the pipeline runner's
        ``_render_agent_activity_line`` and the activity-router's
        ``render_event_line`` so the same logical event renders
        identically regardless of which path produced it (AC-06/AC-07).
        """
        metadata = {} if metadata is None else metadata
        text_content = content or ""

        tool_signature: tuple[str, str] | None = None

        if kind is ActivityEventKind.TOOL_USE:
            # Subscriber delivery still needs the raw tool name +
            # structured input fields so audit/recap paths keep
            # working. Rendering flows through the registry; delivery
            # decisions (record_activity) stay here.
            input_obj = metadata.get("input", metadata.get("args"))
            input_dict: dict[str, object] = (
                cast("dict[str, object]", input_obj) if isinstance(input_obj, dict) else {}
            )
            original_name = text_content
            tool_path = str(input_dict.get("path", "") or "")
            tool_workdir = str(input_dict.get("workdir", "") or "")
            tool_command = str(input_dict.get("command", "") or "")
            tool_pattern = str(input_dict.get("pattern", "") or "")
            tool_signature = (original_name, tool_path)
            # Subscriber receives the registry-rendered text so the
            # recorded line matches what the operator sees in the log.
            sub_line = render_event_kind_text(
                kind,
                text_content,
                metadata=metadata,
                agent_name=unit_id,
            )
            with contextlib.suppress(Exception):
                self._subscriber.record_activity(
                    unit_id=unit_id,
                    line=sub_line,
                    tool_name=original_name,
                    path=tool_path or None,
                    workdir=tool_workdir or None,
                    command=tool_command or None,
                    pattern=tool_pattern or None,
                )

        # ALL formatting goes through the registry -- the friendly name,
        # formatted input, agent prefix, and non-color icon + label
        # carrier all come from ``render_event_kind_text`` so this
        # path cannot drift from the pipeline runner's path. Use a
        # large ``max_chars`` so the registry's own cell-aware
        # truncation never fires here: the condenser owns the
        # soft/hard overflow path (so an over-soft-limit line still
        # picks up the ``[see .agent/raw/unit-N.log]`` ref), and the
        # registry's default 200-cell cap would otherwise pre-truncate
        # short of ``soft_limit`` and silently bypass overflow tracking.
        text = render_event_kind_text(
            kind,
            text_content,
            metadata=metadata,
            agent_name=unit_id,
            max_chars=self._ctx.condenser_hard_limit + 256,
        )

        overflow = self._get_overflow_log(unit_id)
        overflow_ref = overflow.relative_reference(self._workspace_root)

        visible, condensed_flag, summary_line, ai_summary_line = cast(
            "tuple[str, bool, str | None, str | None]",
            condense_content(
                text,
                options=CondenseOptions(
                    soft_limit=self._ctx.condenser_soft_limit,
                    hard_limit=self._ctx.condenser_hard_limit,
                    summary=True,
                    overflow_ref=overflow_ref,
                ),
            ),
        )

        if condensed_flag:
            overflow.append(text)
            self._check_overflow_size(unit_id, overflow)

        effective_summary_line = summary_line
        if (
            kind is ActivityEventKind.TOOL_RESULT
            and summary_line is None
            and text.strip()
            and len(text) >= self._ctx.tool_result_headline_min_chars
        ):
            effective_summary_line = build_headline_or_placeholder(
                text, max_chars=self._ctx.headline_max_chars
            )

        self.emit_activity_line(
            unit_id,
            kind.value,
            visible,
            options=_ActivityLineOptions(
                condensed_ref=overflow_ref if condensed_flag else None,
                condensed_flag=condensed_flag,
                summary_line=effective_summary_line,
                ai_summary_line=ai_summary_line,
                tool_signature=tool_signature,
            ),
        )

        self._emit_drop_warning(unit_id)

    @property
    def activity_router(self) -> ActivityRouter:
        return self._activity_router

    @property
    def subscriber(self) -> PipelineSubscriber:
        return self._subscriber

    def start(self) -> None:
        # Bring the persistent Status Bar up first so banners and progress
        # lines render above the Live region's reserved row. The bar is a
        # no-op on non-tty consoles and in quiet mode (see StatusBar._gate).
        self._status_bar.start()

    def stop(self) -> None:
        # Tear down the Status Bar suppressingly so a Live region error
        # never blocks run-end flushing. Closing the bar before flush_blocks
        # means the final summary prints into clean scrollback with the
        # transient region already erased.
        with contextlib.suppress(Exception):
            self._status_bar.stop()
        self.flush_blocks()

    def update_status_bar(self, model: object) -> None:
        """Push a new :class:`StatusBarModel` to the composed StatusBar.

        Outside the one-shot emit_* surface; reachable through
        ``ParallelDisplay``. No-op when the bar is inactive (the model is
        still stored so the next render can pick it up).
        """
        from ralph.display.status_bar import StatusBarModel

        if not isinstance(model, StatusBarModel):
            model_type = type(model).__name__
            msg = f"update_status_bar requires a StatusBarModel, got {model_type}"
            raise TypeError(msg)
        self._status_bar.update(model)

    @property
    def status_bar(self) -> object:
        """Return the composed :class:`StatusBar` (owner of the persistent footer)."""
        return self._status_bar

    def emit(self, unit_id: str | None, line: str) -> None:
        """Emit a raw line directly to the consolidated log renderer.

        Bare lifecycle tokens (e.g. prefixed transcript noise) are silently
        dropped before reaching the renderer. If unit_id is None, defaults to "run".
        """
        if self._is_quiet:
            return
        if _is_bare_lifecycle(line):
            return
        if unit_id is not None:
            with contextlib.suppress(Exception):
                self._subscriber.record_activity(
                    unit_id=unit_id,
                    line=strip_markup(line),
                    agent_name=unit_id,
                )
        self.emit_log_line(unit_id or "run", line)

    def emit_parsed_event(
        self,
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        metadata: dict[str, object],
    ) -> None:
        """Route a pre-parsed agent event through the structured activity path."""
        if (
            kind in (ActivityEventKind.LIFECYCLE, ActivityEventKind.UNKNOWN)
            and content is not None
            and _is_bare_lifecycle(content)
        ):
            return
        self._emit_activity_event(unit_id, kind, content, None, metadata)

    def set_status(self, unit_id: str, status: WorkerStatus) -> None:
        if self._is_quiet:
            return
        self.emit_status_line(unit_id, str(status))

    def emit_analysis_result(
        self,
        phase: str,
        decision: str,
        reason: str | None = None,
    ) -> None:
        """Emit the analysis-cycle result line.

        Composed of an INFO/META header and a body that names the phase,
        decision, and optional reason; the style is decided by the
        phase_style_for_phase helper.
        """
        with contextlib.suppress(Exception):
            self._subscriber.record_analysis(phase, decision, reason)

    def _emit_section_rule(self, tag: str) -> None:
        """Emit a visual section break (rule line) for the given log-line tag.

        Uses the ``rule`` glyph from DisplayContext (Unicode ``───`` in
        Unicode mode, ASCII ``---`` in ASCII fallback mode). The tag
        appears in the message so log parsers can still locate the
        section boundary even if they don't render the rule glyph.

        The rule glyph carries the theme.banner.border style (sky-blue) and
        the tag suffix carries the theme.banner.title style (bold sky-blue).
        A blank line is emitted before the rule to give the transcript
        visual breathing room. The rule itself never wraps (``overflow=ignore``).
        """
        with contextlib.suppress(Exception):
            self._console.print()  # blank line BEFORE the section rule
            rule_text = _RichText()
            rule_text.append(self._ctx.glyph_for("rule"), style="theme.banner.border")
            rule_text.append(f" {tag}", style="theme.banner.title")
            self._console.print(rule_text, highlight=False, overflow="ignore")

    def emit_run_start(self, orientation: RunStartOrientation) -> None:
        """Emit a one-time run-start orientation block at pipeline start."""
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[run-start]")
            timestamp = self._format_timestamp(self._clock())

            t = _RichText()
            t.append(f"{timestamp} ")
            t.append("MILESTONE", style="theme.level.milestone")
            t.append(" ")
            t.append("META", style="theme.cat.meta")
            t.append(" ")
            t.append(
                f"[run-start] {self._ctx.glyph_for('milestone')} ",
                style="theme.banner.ascii",
            )
            t.append("Ralph Workflow run start", style="theme.banner.title")
            self._console.print(t, markup=False, highlight=False, no_wrap=True)

            if orientation.legend_enabled:
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

            self._emit_run_start(timestamp, orientation)

    def _emit_run_start(self, timestamp: str, orientation: RunStartOrientation) -> None:
        """Emit the run-start orientation body (single default-mode layout)."""
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

        iter_parts: list[str] = []
        if orientation.developer_iters is not None:
            iter_parts.append(f"dev:{orientation.developer_iters}")
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
        """Start timing a new phase and reset its counters."""
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._phase_counters = _PhaseCounters(start_time=self._monotonic())
            self._last_phase_artifact_outcome = ""
            self._phase_close_emitted = False
            if self._run_start_time is None:
                self._run_start_time = self._monotonic()

    @property
    def last_phase_elapsed_seconds(self) -> float:
        """Return elapsed time of the most recently closed phase in seconds."""
        return self._last_phase_elapsed_seconds

    @property
    def last_phase_counters(self) -> _PhaseCounters | None:
        """Return the counters from the most recently closed phase, if available.

        Returns None when no phase has been closed yet.
        """
        return self._last_phase_saved_counters

    @property
    def last_phase_artifact_outcome(self) -> str:
        """Return the artifact outcome from the most recently closed phase."""
        return self._last_phase_artifact_outcome

    @property
    def phase_close_emitted(self) -> bool:
        """Return True when emit_phase_close_from_exit was called for the current phase."""
        return self._phase_close_emitted

    def record_artifact_outcome(self, outcome: str) -> None:
        """Record artifact outcome without emitting a log line."""
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._last_phase_artifact_outcome = outcome

    def emit_phase_close(
        self,
        phase: str,
        produced: str,
        *,
        options: PhaseCloseOptions | None = None,
        phase_role: str | None = None,
        iteration_context: PhaseIterationContext | None = None,
        exit_trigger: str | None = None,
    ) -> None:
        """Emit a single-line recap at the end of a phase."""
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[phase-close]")
            if options is None:
                options = PhaseCloseOptions(
                    phase_role=phase_role,
                    iteration_context=iteration_context,
                    exit_trigger=exit_trigger,
                )
            self._emit_phase_close_body(phase, produced, options=options)

    def _emit_phase_close_body(
        self,
        phase: str,
        produced: str,
        *,
        options: PhaseCloseOptions | None = None,
    ) -> None:
        """Inlined PlainLogRenderer.emit_phase_close body."""
        opts = options or PhaseCloseOptions()
        self.flush_blocks()
        timestamp = self._format_timestamp(self._clock())
        clean_produced = _sanitize(produced).strip()
        counters = self._phase_counters
        if counters is not None:
            elapsed_s = round(max(0.0, self._monotonic() - counters.start_time), 1)
        else:
            elapsed_s = 0.0
            counters = _PhaseCounters()
        if opts.counter_overrides is not None:
            cb = (
                opts.counter_overrides.content_blocks
                if opts.counter_overrides.content_blocks
                else counters.content_blocks
            )
            tb = (
                opts.counter_overrides.thinking_blocks
                if opts.counter_overrides.thinking_blocks
                else counters.thinking_blocks
            )
            tc = (
                opts.counter_overrides.tool_calls
                if opts.counter_overrides.tool_calls
                else counters.tool_calls
            )
            err = (
                opts.counter_overrides.errors if opts.counter_overrides.errors else counters.errors
            )
        else:
            cb = counters.content_blocks
            tb = counters.thinking_blocks
            tc = counters.tool_calls
            err = counters.errors
        exit_part = f" exit={opts.exit_trigger}" if opts.exit_trigger is not None else ""
        suffix = (
            f"{exit_part} (elapsed={format_elapsed_seconds(elapsed_s)}, content_blocks={cb},"
            f" thinking_blocks={tb}, tool_calls={tc},"
            f" errors={err})"
        )
        glyph_prefix = (
            f"{self._ctx.glyph_for('milestone')} "
            if opts.phase_role is not None and LEVELS.get(opts.phase_role) == "MILESTONE"
            else ""
        )
        iter_labels = ""
        if opts.iteration_context is not None and opts.iteration_context.has_context():
            iter_labels = " " + " ".join(
                f"[{label}]" for label, _ in opts.iteration_context.context_labels()
            )
        if clean_produced:
            line_suffix = (
                f"[phase-close] {glyph_prefix}phase={phase}{iter_labels} {clean_produced}{suffix}"
            )
        else:
            line_suffix = f"[phase-close] {glyph_prefix}phase={phase}{iter_labels}{suffix}"
        self._console.print(
            self._build_line(timestamp, "INFO", "META", line_suffix),
            markup=False,
            highlight=False,
            no_wrap=True,
        )
        self._last_phase_saved_counters = counters
        self._last_phase_elapsed_seconds = elapsed_s
        self._phase_counters = None

    def emit_phase_close_from_exit(self, exit_model: PhaseExitModel) -> None:
        """Emit a phase-close recap from a PhaseExitModel."""
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._last_phase_artifact_outcome = exit_model.artifact_outcome
            self._phase_close_emitted = True
            iter_ctx = exit_model.to_iteration_context()
            counter_overrides = None
            if (
                exit_model.content_blocks > 0
                or exit_model.thinking_blocks > 0
                or exit_model.tool_calls > 0
                or exit_model.errors > 0
            ):
                counter_overrides = _PhaseCloseCounters(
                    content_blocks=exit_model.content_blocks,
                    thinking_blocks=exit_model.thinking_blocks,
                    tool_calls=exit_model.tool_calls,
                    errors=exit_model.errors,
                )
            self._emit_phase_close_body(
                exit_model.phase_name,
                exit_model.artifact_outcome,
                options=PhaseCloseOptions(
                    phase_role=exit_model.phase_role,
                    iteration_context=iter_ctx if iter_ctx.has_context() else None,
                    exit_trigger=exit_model.exit_trigger,
                    counter_overrides=counter_overrides,
                ),
            )
            if exit_model.waiting_status_line or exit_model.last_failure_category:
                timestamp = self._format_timestamp(self._clock())
                debug_parts: list[str] = []
                if exit_model.waiting_status_line:
                    debug_parts.append(f"waiting={_sanitize(exit_model.waiting_status_line)}")
                if exit_model.last_failure_category:
                    debug_parts.append(
                        f"failure_category={_sanitize(exit_model.last_failure_category)}"
                    )
                self._console.print(
                    self._build_line(
                        timestamp,
                        "WARN",
                        "META",
                        f"[phase-close] debug phase={exit_model.phase_name} "
                        f"{' '.join(debug_parts)}",
                    ),
                    markup=False,
                    highlight=False,
                    no_wrap=True,
                )
            if exit_model.review_issues_found is not None:
                timestamp = self._format_timestamp(self._clock())
                if exit_model.review_issues_found:
                    review_text = "[phase-close] review: issues found"
                else:
                    review_text = "[phase-close] review: clean"
                self._console.print(
                    self._build_line(timestamp, "INFO", "META", review_text),
                    markup=False,
                    highlight=False,
                    no_wrap=True,
                )

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
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[run-end]")
            self.flush_blocks()
            timestamp = self._format_timestamp(self._clock())
            total_elapsed_s = 0.0
            if self._run_start_time is not None:
                total_elapsed_s = round(max(0.0, self._monotonic() - self._run_start_time), 1)
            elapsed_str = format_elapsed_seconds(total_elapsed_s)

            t = _RichText()
            t.append(f"{timestamp} ")
            t.append("MILESTONE", style="theme.level.milestone")
            t.append(" ")
            t.append("META", style="theme.cat.meta")
            t.append(" ")
            t.append(
                f"[run-end] {self._ctx.glyph_for('milestone')} ",
                style="theme.banner.ascii",
            )
            t.append("Ralph Workflow run end", style="theme.banner.title")
            self._console.print(t, markup=False, highlight=False, no_wrap=True)
            phase_elapsed = f"[run-end] phase={phase} elapsed={elapsed_str}"
            if exit_trigger is not None:
                phase_elapsed += f" exit={exit_trigger}"
            if outer_dev_iteration is not None:
                phase_elapsed += f" dev_cycle={outer_dev_iteration}"
            self._console.print(
                self._build_line(timestamp, "INFO", "META", phase_elapsed),
                markup=False,
                highlight=False,
                no_wrap=True,
            )
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
            self._console.print()  # blank line AFTER the run-end block

    def emit_completion_summary_panel(
        self,
        snapshot: PipelineSnapshot,
        *,
        options: CompletionSummaryOptions | None = None,
    ) -> None:
        """Emit the end-of-run completion summary panel.

        This is one of the consolidated emit_* methods on the class;
        the canonical set lives in
        ``tests/display/test_parallel_display_drift_prevention.py``.
        The 2-segment ``[run-completion]`` section tag is intentionally
        a companion to ``[run-end]``: ``[run-end]`` is the one-line
        run-stop recap emitted before this method; ``[run-completion]``
        is the full completion panel emitted at the very end of the run.

        Visual-hierarchy contract:

        - Section rule (``[run-completion]``) is emitted unconditionally
          (single default-mode layout).
        - The body is delegated to
          :func:`ralph.display.completion_summary.render_completion_summary_group`
          and printed via ``self._console.print(group, ...)``.
        - The body itself begins with a titled Rule
          (``Pipeline Complete`` / ``Pipeline Failed``); the adjacent
          section rule and body title Rule are intentional visual
          punctuation and match the layering pattern used by
          :meth:`emit_phase_transition` (section rule + transition
          banner) and :meth:`emit_phase_close_banner` (section rule +
          body that contains titled Rules).
        - The section rule is the stable log-line tag for downstream
          parsers; the body title Rule is the human-readable title.

        Quiet-mode contract:

        Unlike every other emit_* method, this method
        intentionally does NOT short-circuit on ``self._is_quiet``. The
        completion summary is the only dashboard surface that must
        remain visible in ``--quiet`` mode so the user can see the final
        pipeline result without re-running with non-quiet verbosity.
        ``test_runner_quiet_mode.py::test_quiet_mode_suppresses_dashboard_header_and_phase_banners``
        and
        ``tests/integration/test_transcript_end_to_end.py::test_quiet_mode_suppresses_run_start_and_phase_close``
        pin this contract.

        Args:
            snapshot: The pipeline snapshot to render.
            options: Optional :class:`CompletionSummaryOptions` instance.
                When ``None`` (the default), a fresh
                ``CompletionSummaryOptions()`` is constructed.
        """
        with contextlib.suppress(Exception):
            self._emit_section_rule("[run-completion]")
            from ralph.display.completion_summary import (
                CompletionSummaryOptions,
                render_completion_summary_group,
            )

            resolved_options = options if options is not None else CompletionSummaryOptions()
            group = render_completion_summary_group(
                snapshot,
                display_context=self._ctx,
                options=resolved_options,
            )
            self._console.print(group, markup=False, highlight=False)

    # -- Phase banner methods (port of phase_banner.py) ---------------------
    # All four methods route through self._console.print. Each method calls
    # self._emit_section_rule unconditionally; the single default-mode
    # layout always emits section rules.

    def emit_phase_start(
        self,
        phase: str,
        *,
        agent_name: str | None = None,
        pipeline_policy: PipelinePolicy | None = None,
    ) -> None:
        """Display the start of a pipeline phase (no iteration context).

        Port of :func:`ralph.display.phase_banner.show_phase_start`.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[phase-start]")
            c = self._console
            style = _phase_style(phase, pipeline_policy)
            label = _phase_label(phase)
            line = Text()
            start_glyph = self._ctx.glyph_for("start")
            line.append(f"{start_glyph} ", style=style)
            line.append(label, style=style)
            if agent_name is not None:
                line.append(f"  agent={agent_name}", style="theme.text.muted")
            c.print(line)

    def emit_phase_start_from_entry(
        self,
        entry: PhaseEntryModel,
        *,
        pipeline_policy: PipelinePolicy | None = None,
    ) -> None:
        """Display the start of a pipeline phase from a lifecycle entry model.

        Port of :func:`ralph.display.phase_banner.show_phase_start_from_entry`.
        Canonical model-based path (single default-mode layout): emits a
        titled Rule with phase label, outer development iteration,
        inner analysis iteration, and an optional agent line.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[phase-start]")
            c = self._console
            style = _phase_style(entry.phase_name, pipeline_policy)
            label = entry.human_label()
            start_glyph = self._ctx.glyph_for("start")
            od_glyph = self._ctx.glyph_for("outer_dev")
            ia_glyph = self._ctx.glyph_for("inner_analysis")

            rule_title = Text()
            rule_title.append(f"{start_glyph} ", style=style)
            rule_title.append(label, style=style)
            if entry.outer_dev_iteration is not None:
                rule_title.append(
                    _build_outer_iteration_suffix(
                        entry.outer_dev_iteration,
                        entry.outer_dev_cap,
                        od_glyph=od_glyph,
                        qualifier="(outer)",
                    ),
                    style="theme.outer_dev",
                )
            if entry.inner_analysis is not None:
                rule_title.append(
                    _build_inner_analysis_suffix(
                        entry.inner_analysis,
                        entry.inner_analysis_cap,
                        ia_glyph=ia_glyph,
                        qualifier="(inner)",
                    ),
                    style="theme.inner_analysis",
                )
            if entry.inner_analysis is not None and entry.inner_analysis_cap is not None:
                remaining = entry.inner_analysis_cap - entry.inner_analysis
                if remaining > 0:
                    rule_title.append(f"  [{remaining} left]", style="theme.text.muted")
                elif remaining == 0:
                    rule_title.append("  [last]", style="theme.level.warn")
            c.print(Rule(title=rule_title, style=style))
            if entry.agent_name is not None:
                agent_line = Text()
                agent_line.append("    agent: ", style="theme.text.muted")
                agent_line.append(entry.agent_name, style="theme.text.emphasis")
                c.print(agent_line)

    def emit_phase_transition(
        self,
        from_phase: str,
        to_phase: str,
        *,
        context: dict[str, object] | None = None,
        pipeline_policy: PipelinePolicy | None = None,
    ) -> None:
        """Display a visual transition between pipeline phases.

        Port of :func:`ralph.display.phase_banner.show_phase_transition`.
        Major transitions get a prominent Rule banner; minor transitions get
        a simple titled Rule. The leading section rule is always emitted in
        the single default mode (no per-mode gating remains).
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            c = self._console
            style = _phase_style(to_phase, pipeline_policy)
            from_label = _phase_label(from_phase)
            to_label = _phase_label(to_phase)
            is_major = _resolve_transition_meta(from_phase, to_phase, pipeline_policy)
            ctx = self._ctx
            if is_major:
                self._emit_section_rule("[phase-transition]")
                title = Text()
                title.append(from_label, style="theme.text.muted")
                title.append(f" {ctx.glyph_for('arrow')} ", style="theme.text.emphasis")
                title.append(to_label, style=style)
                if context:
                    detail = "  ".join(format_transition_context_items(context))
                    title.append(f"  ({detail})", style="theme.text.muted")
                c.print(Rule(title=title, style=style))
                return

            self._emit_section_rule("[phase-transition]")
            title = Text()
            arrow = ctx.glyph_for("arrow")
            title.append(f"{from_label} {arrow} {to_label}")
            c.print(Rule(title=title, style=style))

    def emit_phase_close_banner(
        self,
        exit_model: PhaseExitModel,
        *,
        pipeline_policy: PipelinePolicy | None = None,
    ) -> None:
        """Display the close of a pipeline phase from a lifecycle exit model.

        Port of :func:`ralph.display.phase_banner.show_phase_close_banner`.
        The rich, model-based phase-close banner (full stats line, review
        outcome, debug breadcrumb, and trailing titled Rule).

        .. note::
           This method is semantically distinct from the existing
           :meth:`emit_phase_close` (one-line recap) and
           :meth:`emit_phase_close_from_exit` (one-line recap from a
           ``PhaseExitModel``). The two recap methods stay unchanged; this
           banner method is the rich, model-based close banner. Do not
           collapse the three methods.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[phase-close]")
            c = self._console
            style = _phase_style(exit_model.phase_name, pipeline_policy)
            label = _phase_label(exit_model.phase_name)
            line = Text()
            success_glyph = self._ctx.glyph_for("success")
            od_glyph = self._ctx.glyph_for("outer_dev")
            ia_glyph = self._ctx.glyph_for("inner_analysis")
            arrow = self._ctx.glyph_for("arrow")
            line.append(f"{success_glyph} ", style=style)
            line.append(label, style=style)

            if exit_model.outer_dev_iteration is not None:
                suffix = _build_outer_iteration_suffix(
                    exit_model.outer_dev_iteration,
                    exit_model.outer_dev_cap,
                    od_glyph=od_glyph,
                    qualifier="(outer)",
                )
                line.append(suffix, style="theme.outer_dev")

            if exit_model.inner_analysis is not None:
                suffix = _build_inner_analysis_suffix(
                    exit_model.inner_analysis,
                    exit_model.inner_analysis_cap,
                    ia_glyph=ia_glyph,
                    qualifier="(inner)",
                )
                line.append(suffix, style="theme.inner_analysis")

            if exit_model.elapsed_seconds > 0:
                line.append(
                    f"  {format_elapsed_seconds(exit_model.elapsed_seconds)}",
                    style="theme.text.muted",
                )

            if exit_model.exit_trigger is not None:
                line.append(f"  {arrow} {exit_model.exit_trigger}", style="theme.text.muted")

            c.print(line)

            stats_line = self._build_phase_close_stats_line(exit_model)
            if stats_line is not None:
                c.print(stats_line)

            if exit_model.artifact_outcome:
                artifact_line = Text()
                artifact_line.append("    \u21b3 artifact: ", style="theme.text.muted")
                artifact_line.append(exit_model.artifact_outcome, style="theme.text.emphasis")
                c.print(artifact_line)

            review_line = self._build_review_outcome_line(exit_model)
            if review_line is not None:
                c.print(review_line)

            if exit_model.routing_note is not None:
                routing_line = Text()
                routing_line.append(f"  {arrow} ", style="theme.text.muted")
                routing_line.append(exit_model.routing_note, style="theme.level.warn")
                c.print(routing_line)

            debug_line = self._build_debug_line(exit_model)
            if debug_line is not None:
                c.print(debug_line)

            self._print_section_close_rule(
                style,
                c,
                elapsed_seconds=exit_model.elapsed_seconds,
                exit_trigger=exit_model.exit_trigger,
                arrow=arrow,
            )

    def _build_phase_close_stats_line(self, exit_model: PhaseExitModel) -> Text | None:
        """Return an activity-stats supplementary line for the phase-close banner."""
        total = (
            exit_model.content_blocks
            + exit_model.thinking_blocks
            + exit_model.tool_calls
            + exit_model.errors
        )
        if total == 0:
            return None
        stats = Text()
        stats.append("    \u21b3 stats: ", style="theme.text.muted")
        parts: list[tuple[str, str]] = [
            (f"content={exit_model.content_blocks}", "theme.text.muted"),
            (f"thinking={exit_model.thinking_blocks}", "theme.text.muted"),
            (f"tools={exit_model.tool_calls}", "theme.text.muted"),
        ]
        if exit_model.errors > 0:
            parts.append((f"errors={exit_model.errors}", "theme.level.error"))
        for i, (part_text, part_style) in enumerate(parts):
            if i > 0:
                stats.append("  ", style="theme.text.muted")
            stats.append(part_text, style=part_style)
        return stats

    def _build_review_outcome_line(self, exit_model: PhaseExitModel) -> Text | None:
        """Return a review outcome line if review_issues_found is set."""
        if exit_model.review_issues_found is None:
            return None
        review_line = Text()
        review_glyph_pass = self._ctx.glyph_for("review_pass")
        review_glyph_fail = self._ctx.glyph_for("review_fail")
        if exit_model.review_issues_found:
            review_line.append(f"    {review_glyph_fail} ", style="theme.review_fail")
            review_line.append("review: ", style="theme.text.muted")
            review_line.append("issues found", style="theme.level.error")
        else:
            review_line.append(f"    {review_glyph_pass} ", style="theme.review_pass")
            review_line.append("review: ", style="theme.text.muted")
            review_line.append("clean", style="theme.status.success")
        return review_line

    def _build_debug_line(self, exit_model: PhaseExitModel) -> Text | None:
        """Return a debug breadcrumb line if waiting status or failure category is set."""
        if not exit_model.waiting_status_line and not exit_model.last_failure_category:
            return None
        debug_line = Text()
        warning_glyph = self._ctx.glyph_for("warning")
        debug_parts: list[str] = []
        if exit_model.waiting_status_line:
            debug_parts.append(f"waiting: {exit_model.waiting_status_line[:80]}")
        if exit_model.last_failure_category:
            debug_parts.append(f"failure: {exit_model.last_failure_category}")
        debug_line.append(f"  {warning_glyph} debug: ", style="theme.level.warn")
        debug_line.append(" | ".join(debug_parts), style="theme.text.muted")
        return debug_line

    @staticmethod
    def _print_section_close_rule(
        style: str,
        console: Console,
        *,
        elapsed_seconds: float = 0.0,
        exit_trigger: str | None = None,
        arrow: str = "\u2192",
    ) -> None:
        """Print the trailing titled Rule as the section-close separator.

        Renamed from ``_print_wide_close_rule`` after the wt-028-display
        consolidation: the runtime no longer branches by mode, so the
        ``wide`` qualifier no longer describes any runtime branch. The
        section-close Rule renders identically in the single default
        mode.
        """
        parts: list[str] = []
        if elapsed_seconds > 0:
            parts.append(format_elapsed_seconds(elapsed_seconds))
        if exit_trigger is not None:
            parts.append(f"{arrow} {exit_trigger}")
        if parts:
            console.print(Rule(title="  ".join(parts), style=style))
        else:
            console.print(Rule(style=style))

    # -- Artifact renderer methods (port of artifact_renderer.py) ----------
    # All seven methods route through self._console.print. The six titled-block
    # methods also call self._emit_section_rule so the visual hierarchy is
    # unified with the rest of the transcript.

    def emit_plan_artifact(self, workspace_root: Path) -> None:
        """Render the agent-facing plan handoff, falling back to the JSON summary.

        Port of :func:`ralph.display.artifact_renderer.render_plan_artifact`.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[plan]")
            markdown = self._resolve_authoritative_markdown_handoff(
                workspace_root,
                "plan",
                workspace_root / _ARTIFACTS_DIR / "plan.json",
            )
            if markdown:
                self._render_text_block("PLAN", markdown, "execution")
                return
            plan = read_plan_artifact(workspace_root)
            if plan is None:
                self.emit_missing_plan_hint()
                return
            lines: list[str] = []
            if plan.summary:
                lines.append(f"  Context: {plan.summary}")
            if plan.scope_items:
                lines.append("  Scope:")
                lines.extend(f"    - {item}" for item in plan.scope_items)
            if plan.total_steps > 0:
                lines.append(f"  Steps: {plan.total_steps}")
            if plan.risks_mitigations:
                lines.append("  Risks:")
                lines.extend(f"    - {risk}" for risk in plan.risks_mitigations)
            self._render_titled_lines("PLAN", "execution", lines)

    def emit_development_artifact(self, workspace_root: Path) -> None:
        """Render development results using the authoritative Markdown handoff.

        Port of :func:`ralph.display.artifact_renderer.render_development_artifact`.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[development-result]")
            markdown = self._resolve_authoritative_markdown_handoff(
                workspace_root,
                "development_result",
                workspace_root / _ARTIFACTS_DIR / "development_result.json",
            )
            if markdown:
                self._render_text_block("DEVELOPMENT RESULT", markdown, "execution")
                return
            found = self._read_json_defensive(
                workspace_root / _ARTIFACTS_DIR / "development_result.json"
            )
            if found is None:
                return
            self._render_text_block(
                "DEVELOPMENT RESULT",
                json.dumps(found, indent=2),
                "execution",
            )

    def emit_review_artifact(self, workspace_root: Path) -> None:
        """Render review findings using the authoritative Markdown handoff.

        Port of :func:`ralph.display.artifact_renderer.render_review_artifact`.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[review]")
            markdown = self._resolve_authoritative_markdown_handoff(
                workspace_root,
                "issues",
                workspace_root / _ARTIFACTS_DIR / "issues.json",
            )
            if markdown:
                self._render_text_block("REVIEW ISSUES", markdown, "review")
                return
            found = self._read_json_defensive(workspace_root / _ARTIFACTS_DIR / "issues.json")
            if found is None:
                return
            self._render_text_block(
                "REVIEW ISSUES",
                json.dumps(found, indent=2),
                "review",
            )

    def emit_fix_artifact(self, workspace_root: Path) -> None:
        """Render fix result artifacts as a titled block.

        Port of :func:`ralph.display.artifact_renderer.render_fix_artifact`.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[fix]")
            markdown = self._resolve_authoritative_markdown_handoff(
                workspace_root,
                "fix_result",
                workspace_root / _ARTIFACTS_DIR / "fix_result.json",
            )
            if markdown:
                self._render_text_block("FIX", markdown, "fix")
                return
            found = self._first_json_candidate(
                workspace_root / _ARTIFACTS_DIR / "fix_result.json",
                workspace_root / _ARTIFACTS_DIR / "issues.json",
            )
            if found is None:
                return
            lines = self._render_fix_json_summary(found)
            self._render_titled_lines("FIX", "fix", lines)

    def emit_analysis_decision(self, workspace_root: Path, drain: str) -> None:
        """Render an analysis decision artifact as a titled block.

        Port of :func:`ralph.display.artifact_renderer.render_analysis_decision`.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[analysis]")
            artifact_type = self._analysis_handoff_artifact_type(drain)
            if artifact_type is not None:
                markdown = self._resolve_authoritative_markdown_handoff(
                    workspace_root,
                    artifact_type,
                    workspace_root / _ARTIFACTS_DIR / f"{artifact_type}.json",
                )
                if markdown:
                    self._render_text_block(f"ANALYSIS: {drain}", markdown, "analysis")
                    return
            summary = read_latest_analysis_decision(workspace_root, drain)
            if summary is None:
                return
            lines = [f"  decision: {summary.decision}"]
            if summary.reason:
                lines.append(f"  reason: {summary.reason}")
            self._render_titled_lines(f"ANALYSIS: {drain}", "analysis", lines)

    def emit_commit_message(self, workspace_root: Path) -> None:
        """Render the commit message artifact as a titled block.

        Port of :func:`ralph.display.artifact_renderer.render_commit_message`.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[commit-message]")
            try:
                message = read_commit_message_artifact(workspace_root)
            except Exception:
                message = None
            if message is None:
                return
            self._render_text_block("COMMIT MESSAGE", message, "commit", indent=True)

    def emit_missing_plan_hint(self) -> None:
        """Emit a plain INFO line when the plan artifact is absent at phase completion.

        Port of :func:`ralph.display.artifact_renderer.render_missing_plan_hint`.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            timestamp = datetime.now(UTC).isoformat()
            self._console.print(
                f"{timestamp} INFO META [plan] (no plan artifact on disk)",
                markup=False,
                highlight=False,
                no_wrap=True,
            )

    @staticmethod
    def _analysis_handoff_artifact_type(drain: str) -> str:
        return f"{drain}_decision"

    @staticmethod
    def _read_text_defensive(path: Path) -> str | None:
        try:
            content = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError, PermissionError):
            return None
        return content

    @staticmethod
    def _read_markdown_handoff(workspace_root: Path, artifact_type: str) -> str | None:
        relative_path = handoff_path_for_artifact(artifact_type)
        if relative_path is None:
            return None
        candidate = workspace_root / relative_path
        markdown = ParallelDisplay._read_text_defensive(candidate)
        if markdown is None:
            return None
        stripped = markdown.strip()
        return stripped or None

    @staticmethod
    def _regenerated_markdown_handoff(
        workspace_root: Path,
        artifact_type: str,
        artifact_path: Path,
    ) -> str | None:
        artifact_content = ParallelDisplay._read_text_defensive(artifact_path)
        if artifact_content is None:
            return None
        try:
            created_path = ensure_markdown_handoff_from_artifact(
                workspace_root,
                artifact_type,
                artifact_content,
            )
        except (json.JSONDecodeError, OSError, PermissionError, TypeError, ValueError):
            return None
        if created_path is None:
            return None
        regenerated = ParallelDisplay._read_text_defensive(Path(created_path))
        if regenerated is None:
            return None
        stripped = regenerated.strip()
        return stripped or None

    @staticmethod
    def _resolve_authoritative_markdown_handoff(
        workspace_root: Path,
        artifact_type: str,
        artifact_path: Path,
    ) -> str | None:
        regenerated = ParallelDisplay._regenerated_markdown_handoff(
            workspace_root, artifact_type, artifact_path
        )
        if regenerated is not None:
            return regenerated
        return ParallelDisplay._read_markdown_handoff(workspace_root, artifact_type)

    def _render_titled_lines(self, title: str, style_phase: str, lines: list[str]) -> None:
        """Render a title rule, the body lines, and a closing rule.

        Body lines are sanitized through :func:`strip_terminal_control`
        so a hostile escape sequence in a handoff body cannot paint the
        real terminal -- but the literal ``[``/``]`` characters common in
        markdown bodies (``[title](url)``) are preserved because that
        sink already uses ``markup=False``.
        """
        self._console.print()
        self._console.print(
            Rule(title, style=_phase_style(style_phase)), markup=False, highlight=False
        )
        for line in lines:
            self._console.print(
                strip_terminal_control(line), markup=False, highlight=False
            )
        self._console.print(Rule(style=_phase_style(style_phase)), markup=False, highlight=False)

    def _render_text_block(
        self,
        title: str,
        body: str,
        style_phase: str,
        *,
        indent: bool = False,
    ) -> None:
        lines = [line.rstrip() for line in body.splitlines() if line.strip()]
        if indent:
            lines = [f"  {lines[0]}", *[f"    {line}" for line in lines[1:]]] if lines else []
        self._render_titled_lines(title, style_phase, lines)

    @staticmethod
    def _read_json_defensive(path: Path) -> dict[str, object] | None:
        raw = ParallelDisplay._read_text_defensive(path)
        if raw is None:
            return None
        try:
            parsed_obj: object = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed_obj, dict):
            return None
        return cast("dict[str, object]", parsed_obj)

    @staticmethod
    def _first_json_candidate(*candidates: Path) -> dict[str, object] | None:
        for candidate in candidates:
            found = ParallelDisplay._read_json_defensive(candidate)
            if found is not None:
                return found
        return None

    @staticmethod
    def _render_fix_json_summary(found: dict[str, object]) -> list[str]:
        if "issues" in found and isinstance(found["issues"], list):
            return ParallelDisplay._render_issues_summary(found["issues"])
        if "fixed" in found:
            return ParallelDisplay._render_fixed_summary(found["fixed"])
        return [f"  Fix artifact: {list(found.keys())[:5]}"]

    @staticmethod
    def _render_issues_summary(issues: list[object]) -> list[str]:
        lines = [f"  {len(issues)} issue(s) addressed:"]
        for issue in issues[:10]:
            if isinstance(issue, dict):
                desc_obj = issue.get("description") or issue.get("message") or str(issue)
            else:
                desc_obj = str(issue)
            lines.append(f"    - {str(desc_obj)[:120]}")
        return lines

    @staticmethod
    def _render_fixed_summary(fixed: object) -> list[str]:
        if isinstance(fixed, list):
            lines = [f"  {len(fixed)} item(s) fixed:"]
            lines.extend(f"    - {str(item)[:120]}" for item in fixed[:10])
            return lines
        return [f"  Fixed: {fixed}"]

    # -- Welcome-banner, first-run-panel, table, capability-summary, status -

    def emit_first_run_panel(self, content: list[RenderableType]) -> None:
        """Print the first-run welcome Panel to ``self._ctx.console``.

        Port of :func:`ralph.display.first_run_panel.render_first_run_panel`.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            panel = Panel(
                Group(*content),
                title="Ralph Workflow first-run setup",
                border_style="theme.banner.border",
                padding=(1, 2),
            )
            self._console.print(panel)

    def emit_welcome_banner(
        self,
        *,
        version: str,
    ) -> None:
        """Print the Ralph Workflow welcome banner.

        Port of :func:`ralph.banner.show_banner`.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[welcome]")
            banner_text = Text("\n".join(_ASCII_ART_BANNER), style="theme.banner.ascii")
            version_text = Text(f"v{version}", style="theme.banner.version")
            title_text = Text("Ralph Workflow", style="theme.banner.title")
            welcome_text = Text(_WELCOME_MESSAGE_TEXT, style="theme.banner.welcome")
            tagline_text = Text(_TAGLINE_TEXT, style="theme.banner.tagline")
            banner_panel = Panel.fit(
                banner_text,
                border_style="theme.banner.border",
                padding=(0, 1),
                title=title_text,
                subtitle=version_text,
            )
            self._console.print(Group(banner_panel, welcome_text, tagline_text))

    def emit_agents_table(self, agents: Mapping[str, object]) -> None:
        """Render the agent table for --list-agents.

        Port of :func:`ralph.cli.options.display_agents_table`.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[agents]")
            table = Table(title="Configured Agents", show_header=True)
            table.add_column("Name", style="theme.cat.meta")
            table.add_column("Command")
            table.add_column("Parser", style="theme.cat.cont")
            table.add_column("Can Commit", justify="center")
            if not agents:
                table.add_row(
                    Text("No agents configured", style="theme.text.muted"), "", "", ""
                )
            else:
                for name, agent in agents.items():
                    cmd = getattr(agent, "cmd", "")  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                    parser = getattr(agent, "json_parser", None)  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                    can_commit = getattr(agent, "can_commit", False)  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                    can_commit_str = "yes" if can_commit else "no"  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                    parser_str = str(parser.value if parser is not None else "")  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                    table.add_row(name, cmd, parser_str, can_commit_str)  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
            self._console.print(table)

    def emit_providers_table(self, providers: list[str]) -> None:
        """Render the providers table for --list-providers.

        Port of :func:`ralph.cli.options.display_providers_table`.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[providers]")
            table = Table(title="Available Providers", show_header=True)
            table.add_column("Provider", style="theme.cat.meta")
            table.add_column("Status", justify="center")
            if not providers:
                table.add_row(Text("No providers available", style="theme.text.muted"), "")
            else:
                for provider in providers:
                    table.add_row(provider, "Available")
            self._console.print(table)

    def emit_config_table(self, config: UnifiedConfig) -> None:
        """Render the effective config panel for --check-config.

        Port of :func:`ralph.display.tables.show_config`.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[config]")
            config_json = config.model_dump_json(indent=2)
            self._console.print(
                Panel(
                    config_json,
                    title="Effective Configuration",
                    border_style="theme.phase.planning",
                )
            )

    def emit_capability_summary(
        self,
        state: CapabilityState,
        *,
        workspace_root: Path | None = None,
    ) -> None:
        """Print the baseline capabilities summary table.

        Port of :func:`ralph.cli._capability_summary.print_capability_summary`.
        The base table and skill-root coverage table are built by the
        standalone helper module (collected via lazy import to avoid a
        circular import). The print side goes through self._console.print
        so the entire transcript is consolidated on ParallelDisplay.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[capabilities]")
            from ralph.cli._capability_summary import collect_skill_root_rows
            from ralph.skills._baseline_catalog import STATIC_BUILTIN_CAPABILITIES
            from ralph.skills._capability_status import CapabilityStatus

            resolved_workspace = Path.cwd() if workspace_root is None else workspace_root
            table = Table(title="Baseline Capabilities", show_header=True)
            table.add_column("Capability", style="theme.cat.meta")
            table.add_column("Type")
            table.add_column("Status")
            for cap in STATIC_BUILTIN_CAPABILITIES:
                table.add_row(
                    cap.name.replace("_", " ").title(),
                    "Built-in",
                    Text("OK \u2014 always available", style="theme.status.success"),
                )
            managed_rows = [
                ("Web search (DuckDuckGo)", state.web_search),
                ("Page retrieval (visit_url)", state.visit_url),
                ("Docs MCP (localhost:6280)", state.docs_mcp),
                ("Skill bundles", state.skills),
            ]
            for label, entry in managed_rows:
                if entry.status == CapabilityStatus.INSTALLED_HEALTHY:
                    status_text = Text("OK", style="theme.status.success")
                elif entry.update_available:
                    status_text = Text(
                        "Update available \u2014 run `ralph --init` to update",
                        style="theme.status.warning",
                    )
                else:
                    status_text = Text(
                        f"{entry.status.value} \u2014 run `ralph --init` or check config",
                        style="theme.status.warning",
                    )
                table.add_row(label, "Managed", status_text)
            self._console.print(table)
            if state.skills.status != CapabilityStatus.NOT_INSTALLED:
                self._console.print(Text("Skill root coverage", style="theme.cat.meta"))
                skill_rows = collect_skill_root_rows(workspace_root=resolved_workspace)
                skill_table = Table(show_header=True)
                skill_table.add_column("Agent", style="theme.cat.meta")
                skill_table.add_column("Skill root", style="theme.text.muted")
                skill_table.add_column("Scope", style="theme.cat.meta")
                skill_table.add_column("Status")
                for agent_label, skill_root, scope, status_text in skill_rows:
                    skill_table.add_row(agent_label, skill_root, scope, status_text)
                self._console.print(skill_table)

    def emit_status(self, message: str) -> None:
        """Emit a status line through the consolidated display.

        Ports the prior ``_status_text`` helper in
        :mod:`ralph.cli.commands.init` (one of the 13+ direct
        ``console.print`` call sites).
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[status]")
            self._console.print(message, markup=False, highlight=False)

    def emit_warning(self, message: str) -> None:
        """Emit a warning line through the consolidated display.

        Ports the prior warning ``console.print`` calls in
        :mod:`ralph.cli.commands.init`.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[warning]")
            # ``soft_wrap=True`` preserves long lines (warnings that mention a
            # concrete file path or a re-run command) without truncating at
            # the terminal width — critical because a clipped warning hides
            # the fix-it phrase the operator needs to act on.
            self._console.print(message, markup=False, highlight=False, soft_wrap=True)

    def emit_skill_failure_warning(self, failures: list[str]) -> None:
        """Emit a single warning line listing the skill-failure entries.

        Ports :func:`ralph.cli.commands.init._print_skill_failure_warning`.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[skill-failure]")
            joined = ", ".join(failures)
            self._console.print(
                Text(
                    f"Skills auto-install reported: {joined}.\n"
                    "Run `ralph --force-init-skills` to repair and overwrite, "
                    "or `ralph --diagnose` for details.",
                    style="theme.status.warning",
                )
            )

    def emit_fallback_next_steps(self, next_steps: list[str]) -> None:
        """Emit the fallback next-steps list.

        Ports :func:`ralph.cli.commands.init._print_fallback_next_steps`.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[next-steps]")
            for index, line in enumerate(next_steps, start=1):
                self._console.print(f"  {index}. {line}", markup=False, highlight=False)

    # -- Consolidated table / panel / info methods (wt-007) ----------------

    def emit_blank_line(self) -> None:
        """Print a single blank line for visual spacing."""
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._console.print()

    def emit_info_panel(self, *, title: str, content: str) -> None:
        """Render a theme.phase.planning bordered info Panel.

        Used by ``diagnose`` to surface the "Next steps" panel and any
        free-form info block. Replaces the inline ``Panel(...)`` call
        in diagnose.py.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[info]")
            panel = Panel(content, title=title, border_style="theme.phase.planning", padding=(1, 2))
            self._console.print(panel)

    def emit_metrics_table(self, metrics: dict[str, int]) -> None:
        """Render the metrics table for pipeline summary stats.

        Port of :func:`ralph.display.tables.show_metrics`.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[metrics]")
            table = Table(
                title="Pipeline Metrics",
                show_header=True,
                expand=True,
                title_style="theme.banner.title",
                header_style="theme.text.emphasis",
            )
            table.add_column("Metric", style="theme.cat.meta")
            table.add_column("Value", justify="right", style="theme.status.success")
            for name, value in metrics.items():
                table.add_row(name, str(value))
            self._console.print(table)

    def emit_checkpoint_summary_table(self, options: object) -> None:
        """Render the checkpoint summary table.

        Port of :func:`ralph.display.tables.show_checkpoint_summary`.
        ``options`` is a ``CheckpointSummaryOptions``-like object with
        ``phase`` (str) and ``budget_progress`` (Mapping[str, tuple[int, int]]).
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[checkpoint]")
            phase: str = getattr(options, "phase", "")
            progress: Mapping[str, tuple[int, int]] = getattr(options, "budget_progress", {})
            table = Table(
                title="Checkpoint Summary",
                show_header=False,
                expand=True,
                title_style="theme.banner.title",
            )
            table.add_column("Property", style="theme.cat.meta")
            table.add_column("Value")
            table.add_row("Phase", str(phase))
            for counter_name, value_pair in progress.items():
                completed, cap = value_pair
                table.add_row(str(counter_name), f"{completed}/{cap}")
            self._console.print(table)

    def emit_diagnose_inventory_table(self, rows: Sequence[tuple[object, ...]]) -> None:
        """Render the diagnose inventory table.

        ``rows`` is a list of tuples; each tuple is one row whose items
        become the cells of that row in column order. The first column
        is the ``Server`` (theme.cat.meta), the second is the ``Origin``,
        the third is the ``Transport`` and the fourth is the ``Exposure``.
        If a row has fewer than 4 cells the missing cells are filled
        with ``"-"``.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[diagnose-inventory]")
            table = Table(
                title="Effective Session MCP Inventory",
                show_header=True,
                title_style="theme.banner.title",
                header_style="theme.text.emphasis",
            )
            table.add_column("Server", style="theme.cat.meta")
            table.add_column("Origin", style="theme.text.muted")
            table.add_column("Transport", style="theme.text.muted")
            table.add_column("Exposure", style="theme.text.muted")
            for row in rows:
                cells = [str(cell) if cell is not None else "-" for cell in row]
                while len(cells) < _INVENTORY_TABLE_COLUMNS:
                    cells.append("-")
                table.add_row(*cells[:_INVENTORY_TABLE_COLUMNS])
            self._console.print(table)

    def emit_diagnose_probe_table(self, rows: Sequence[tuple[object, ...]]) -> None:
        """Render the diagnose probe (transport compatibility) table.

        Each row is a 5-tuple: (server, claude, codex, opencode, agy).
        Missing cells default to ``"-"``.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[diagnose-probe]")
            table = Table(
                title="Agent Transport Compatibility",
                show_header=True,
                title_style="theme.banner.title",
                header_style="theme.text.emphasis",
            )
            table.add_column("Server", style="theme.cat.meta")
            table.add_column("Claude", style="theme.text.muted")
            table.add_column("Codex", style="theme.text.muted")
            table.add_column("OpenCode", style="theme.text.muted")
            table.add_column("AGY", style="theme.text.muted")
            for row in rows:
                cells = [str(cell) if cell is not None else "-" for cell in row]
                while len(cells) < _PROBE_TABLE_COLUMNS:
                    cells.append("-")
                table.add_row(*cells[:_PROBE_TABLE_COLUMNS])
            self._console.print(table)

    def emit_diagnose_servers_table(self, rows: Sequence[tuple[object, ...]]) -> None:
        """Render the diagnose MCP servers (custom health) table.

        Each row is a 5-tuple: (server, transport, status, tools, detail).
        Missing cells default to ``"-"``.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[diagnose-servers]")
            table = Table(
                title="Custom MCP Servers",
                show_header=True,
                title_style="theme.banner.title",
                header_style="theme.text.emphasis",
            )
            table.add_column("Server", style="theme.cat.meta")
            table.add_column("Transport", style="theme.text.muted")
            table.add_column("Status", style="theme.text.muted")
            table.add_column("Tools", style="theme.text.muted")
            table.add_column("Detail", style="theme.text.muted")
            for row in rows:
                cells = [str(cell) if cell is not None else "-" for cell in row]
                while len(cells) < _SERVERS_TABLE_COLUMNS:
                    cells.append("-")
                table.add_row(*cells[:_SERVERS_TABLE_COLUMNS])
            self._console.print(table)

    def emit_dry_run_summary(
        self,
        *,
        phase: str,
        iterations: int,
        details: Mapping[str, object] | None = None,
    ) -> None:
        """Render the dry-run summary block for the run command.

        ``details`` is an optional mapping of extra key/value lines to print
        after the standard phase / iteration lines.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[dry-run]")
            header = Text("Dry run mode", style="theme.cat.meta")
            self._console.print(header)
            self._console.print(
                Text(f"  Phase: {phase}", style="theme.text.muted"),
                markup=False,
                highlight=False,
            )
            self._console.print(
                Text(f"  Iterations: {iterations}", style="theme.text.muted"),
                markup=False,
                highlight=False,
            )
            if details is not None:
                for key, value in details.items():
                    self._console.print(
                        Text(f"  {key}: {value}", style="theme.text.muted"),
                        markup=False,
                        highlight=False,
                    )

    def emit_renderable(self, renderable: object) -> None:
        """Print a pre-built rich Renderable (Table, Panel, Group, ...) through the display.

        Used by ``diagnose`` and ``smoke`` tables whose row shape does not
        match the dedicated ``emit_diagnose_*`` / ``emit_metrics_*``
        helpers. The renderable is printed through ``self._console`` so
        the section-rule contract and quiet-mode suppression still apply.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._console.print(renderable)

    @property
    def display_context(self) -> DisplayContext:
        """Return the DisplayContext this display renders against."""
        return self._ctx

    @property
    def console(self) -> Console:
        """Expose console for external renderers."""
        return self._ctx.console

    def drop_unit(self, unit_id: str) -> None:
        """Release per-unit state so long parallel sessions don't accumulate state across waves.

        Removes the unit's overflow log, overflow-warning flag,
        drop-warning timestamp, last-emitted tool signature, last
        worker-state snapshot, active streaming block, last
        checkpoint char count, and propagates the drop to the embedded
        ``ActivityRouter``. Safe to call for a unit that was never
        added; missing entries are silently skipped.
        """
        overflow = self._overflow_logs.pop(unit_id, None)
        if overflow is not None:
            overflow.close()
        self._overflow_warned.discard(unit_id)
        self._drop_last_warned.pop(unit_id, None)
        self._last_emitted_tool_signature.pop(unit_id, None)
        self._last_worker_states.pop(unit_id, None)
        self._active_block.pop(unit_id, None)
        self._active_block_chars.pop(unit_id, None)
        self._last_checkpoint_chars.pop(unit_id, None)
        self._activity_router.drop_unit(unit_id)

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


def emit_activity_line(
    display: ParallelDisplay | None,
    unit_id: str | None,
    line: str,
    display_context: DisplayContext | None = None,
) -> None:
    """Emit a raw activity line through the given display, or no-op if None.

    Replaces the legacy `emit_display_line` helper from
    `ralph.pipeline.legacy_console_display`. Bare lifecycle lines are
    dropped by ParallelDisplay itself; this helper just routes the line
    to the correct unit_id. When ``display`` is None but a
    ``display_context`` is provided, the line is written to the
    context's console for legacy compatibility.
    """
    if display is None:
        if display_context is None:
            return
        console = display_context.console
        if unit_id is None:
            console.print(_sanitize(line), markup=False, highlight=False)
            return
        console.print(
            f"[{_render_unit_id(unit_id)}] {_sanitize(line)}",
            markup=False,
            highlight=False,
        )
        return
    display.emit(unit_id, line)


def resolve_active_display(
    display: ParallelDisplay | DisplayContext | None,
    display_context: DisplayContext | None = None,
) -> ParallelDisplay:
    """Return the given display, constructing a ParallelDisplay from the context if needed.

    The context is required when `display` is None. Rich is a required
    dependency (declared in `pyproject.toml` line 22: `rich>=13.0`), so
    ParallelDisplay always initialises successfully here.

    A ``DisplayContext`` passed as ``display`` is unwrapped to its
    ``display_context`` slot and a fresh ``ParallelDisplay`` is constructed,
    so callers that only have a context still get a real display.
    """
    if isinstance(display, DisplayContext):
        display_context = display
        display = None
    if display is not None:
        return display
    if display_context is None:
        raise TypeError("display_context is required when display is None")
    return ParallelDisplay(display_context)


def _resolve_active_display_from_context(
    display_context: DisplayContext,
) -> ParallelDisplay:
    """Construct a fresh ParallelDisplay from the supplied context.

    Used by helpers that only have a ``DisplayContext`` (not the original
    display) in scope. Returns a new ParallelDisplay bound to the same
    DisplayContext, so output goes to the same console and theme.
    """
    return ParallelDisplay(display_context)


def resolve_display(
    display: ParallelDisplay | None,
    display_context: DisplayContext | None = None,
    *,
    is_quiet: bool = False,
) -> ParallelDisplay:
    """Return the given display or construct one from the context.

    Single source of truth that replaces the legacy
    ``resolve_display`` helper from
    ``ralph.pipeline.legacy_console_display``. Pass-through for
    non-None inputs; constructs a :class:`ParallelDisplay` from
    the supplied context when ``display`` is ``None``. When
    ``is_quiet=True``, the constructed display short-circuits all
    banner and log-line emissions (see ParallelDisplay quiet-mode
    contract).
    """
    if display is not None:
        return display
    if display_context is None:
        raise TypeError("display_context is required when display is None")
    return ParallelDisplay(display_context, is_quiet=is_quiet)


def status_text(label: str, value: str, style: str) -> str:
    """Build a styled status line as a plain string.

    Replaces the legacy `status_text` helper from
    `ralph.pipeline.legacy_console_display`. Returns plain text — the
    caller passes it through `emit_activity_line` which uses
    ParallelDisplay.emit (plain log routing) for rendering.
    """
    del style  # styling is delegated to the renderer; keep the signature stable.
    return f"{label}: {value}"


def build_default_display_legacy_bridge(
    workspace_root: Path,
    display_context: DisplayContext,
    pipeline_policy: PipelinePolicy | None = None,
    *,
    is_quiet: bool = False,
) -> ParallelDisplay:
    """Construct the default :class:`ParallelDisplay`.

    Single source of truth that replaces the legacy
    ``build_default_display`` helper from
    ``ralph.pipeline.legacy_console_display``. Rich is a verified
    required dependency (declared in ``pyproject.toml`` line 22:
    ``rich>=13.0``) so the construction cannot fail.
    """
    return ParallelDisplay(
        display_context,
        workspace_root=workspace_root,
        run_id=str(uuid.uuid4()),
        pipeline_policy=pipeline_policy,
        is_quiet=is_quiet,
    )


def get_display_context(
    display: object | None,
    display_context: DisplayContext | None = None,
) -> DisplayContext:
    """Return the DisplayContext a caller should render against.

    Single source of truth for the legacy ``get_display_context``
    helper. The display's own context is preferred when present
    (tries ``display_context`` first, then ``_ctx`` for
    back-compat with fakes that store it privately); otherwise
    the caller-provided context is used.
    """
    if display is not None:
        own_context: DisplayContext | None = getattr(display, "display_context", None)
        if own_context is None:
            own_context = getattr(display, "_ctx", None)
        if own_context is not None:
            return own_context
    if display_context is None:
        raise TypeError("display_context is required when display is None")
    return display_context


def subscriber_for_display(
    display: ParallelDisplay | None,
) -> PipelineSubscriber | None:
    """Return the pipeline subscriber attached to the given display, when present."""
    if display is None:
        return None
    return cast("PipelineSubscriber | None", getattr(display, "subscriber", None))


__all__ = [
    "ParallelDisplay",
    "build_default_display_legacy_bridge",
    "emit_activity_line",
    "get_display_context",
    "phase_label",
    "phase_style",
    "resolve_active_display",
    "resolve_display",
    "status_text",
    "strip_markup",
    "subscriber_for_display",
]
