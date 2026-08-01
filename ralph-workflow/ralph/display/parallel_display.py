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
import re
import threading
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

from rich.cells import cell_len
from rich.console import Group
from rich.padding import Padding
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
    _EMPTY_PLAN_SIGNATURE,
    _KIND_TO_LEVEL,
    _KIND_TO_TAG,
    _STREAMING_BLOCK_TAGS,
    _STREAMING_KINDS,
    LEVELS,
    TAG_CATEGORY,
    _sanitize,
)
from ralph.display._streaming_ctx import _StreamingCtx
from ralph.display._tool_correlation import tool_call_id
from ralph.display.activity_model import ActivityEventKind
from ralph.display.activity_router import ActivityRouter
from ralph.display.agent_event_renderer import _format_timestamp as _format_iso_timestamp_hh_mm_ss
from ralph.display.agent_event_renderer import (
    make_event_for_emit,
    render_event,
)
from ralph.display.artifact_reader import (
    read_latest_analysis_decision,
    read_plan_artifact,
)
from ralph.display.content_condenser import CondenseOptions, condense_content
from ralph.display.context import DisplayContext
from ralph.display.edit_preview import (
    build_edit_preview,
    preview_header,
    preview_record_text,
    render_markdown_preview,
)
from ralph.display.lifecycle_filter import is_bare_lifecycle as _is_bare_lifecycle
from ralph.display.line_sanitizer import strip_markup_safe, strip_terminal_control
from ralph.display.phase_status import (
    format_analysis_cycle,
    format_dev_cycle,
    format_elapsed_seconds,
    format_transition_context_items,
)
from ralph.display.presented_entry import outcome_is_failure
from ralph.display.preview_payload import payload_from_tool_event
from ralph.display.raw_overflow import DEFAULT_MAX_OVERFLOW_FILE_BYTES, RawOverflowLog
from ralph.display.record_writer import _INDENT_WIDTH, RenderedRecordWriter, rendered_record_path
from ralph.display.subscriber import PipelineSubscriber
from ralph.display.theme import detect_terminal_background_is_light, diff_fill_styles
from ralph.mcp.artifacts.commit_message import read_commit_message_artifact
from ralph.mcp.artifacts.handoffs import handoff_path_for_artifact

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
_SECONDS_PER_MINUTE: int = 60
_PREVIEW_MAX_LINES: int = 40

# A tool activity is "repeated" (coalesced with a "xN" count in the live status)
# starting from the second consecutive identical call.
_MIN_COALESCE_REPEAT = 2
_MIN_TOOL_RESULT_COLLAPSE_COUNT = 3
_TOOL_RESULT_CHANNEL_RE = re.compile(r"\[tool-result\]\s*")
_TOOL_RESULT_RUNNING_RE = re.compile(r"\s*\(running\.\.\.\)")
_TOOL_RESULT_REPEATED_SEVERITY_AND_IDENTITY_RE = re.compile(r"\b(severity=\S+)\s+\1\s+(\S+)\s+\2\b")


def _clean_tool_result_content(content: str, unit_id: str) -> str:
    """Remove transport residue from a tool result before shared rendering."""
    clean = _TOOL_RESULT_CHANNEL_RE.sub("", content)
    clean = _TOOL_RESULT_RUNNING_RE.sub("", clean)
    clean = _TOOL_RESULT_REPEATED_SEVERITY_AND_IDENTITY_RE.sub(r"\1 \2", clean)
    if unit_id:
        clean = re.sub(rf"\b{re.escape(unit_id)}(?:\s+{re.escape(unit_id)})+\b", unit_id, clean)
    return " ".join(clean.split())


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
    """Strip valid Rich markup and terminal control sequences."""
    return ParallelDisplay.strip_markup(line)


def _strip_markup(line: str) -> str:
    """Reduce valid Rich markup for explicit markup-stripping callers.

    Delegates to :func:`strip_markup_safe` -- the single choke point that owns
    the markup-parse guard, so malformed agent markup (an unmatched
    ``[/pdf /text /imageb]`` closing tag in a grep pattern) cannot raise out
    of the activity emit path.
    """
    return strip_markup_safe(line)


def _record_tool_call_body(record_body: str, source_content: str) -> str:
    """Restore a source continuation carrier removed from a preview header.

    The preview formatter deliberately removes live pairing chrome from a
    structured tool-call header. A source-provided ``↳`` is event content,
    however, and must remain in the greppable rendered record.
    """
    if (
        "↳" in source_content
        and not source_content.lstrip().startswith("↳ ")
        and "↳" not in record_body
    ):
        return f"{record_body} ↳".strip()
    return record_body


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
        "_block_open_mono",
        "_block_open_wall",
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
        "_last_phase_per_unit",
        "_last_phase_saved_counters",
        "_last_plan_signature",
        "_last_recorded_body",
        "_last_text_thinking_block_close",
        "_last_tool_result_content",
        "_last_waiting_signature",
        "_last_worker_states",
        "_monotonic",
        "_overflow_logs",
        "_overflow_warned",
        "_pending_phase_headers",
        "_pending_tool_results",
        "_phase_close_emitted",
        "_phase_counters",
        "_recorded_tool_call_ids",
        "_rendered_writers",
        "_run_counters",
        "_run_start_time",
        "_status_bar",
        "_subscriber",
        "_terminal_bg_is_light",
        "_watchdog_attention",
        "_watchdog_attention_lock",
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
        # Resolve the terminal background ONCE, here, so the pure
        # preview builder never touches env or the tty. The resolution
        # asks the terminal for its actual background colour (OSC 11)
        # and falls back to env hints, so syntax-highlight colours are
        # chosen against the operator's real background rather than an
        # assumed one. Any failure degrades to ``None``, selecting the
        # both-backgrounds-safe unknown palette rather than assuming dark;
        # the probe must never be able to break display construction.
        self._terminal_bg_is_light: bool | None = None
        with contextlib.suppress(Exception):
            self._terminal_bg_is_light = detect_terminal_background_is_light(display_context.env)
        self._clock: Callable[[], datetime] = (
            clock if clock is not None else (lambda: datetime.now(UTC))
        )
        self._monotonic: Callable[[], float] = (
            monotonic if monotonic is not None else time.monotonic
        )
        # wt-047-stall-label: watchdog-sourced attention state for the Status
        # Bar's STALLED slot. The subscriber mirrors every event's
        # authoritative stall assessment through its sink. The host substitutes
        # this value only when pushed attention is None. Thread-safe below.
        self._watchdog_attention: str | None = None
        self._watchdog_attention_lock = threading.Lock()

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
        # Streaming block-open wall + monotonic times so the close entry can
        # carry the sanctioned sketch-J span and duration. Populated in
        # ``_handle_new_streaming_block`` and popped in ``_close_block``;
        # the lifetime never exceeds ``_active_block`` so the bounded-accumulator
        # contract carries through.
        self._block_open_wall: dict[str, datetime] = {}  # bounded-accumulator-ok: _active_block
        self._block_open_mono: dict[str, float] = {}  # bounded-accumulator-ok: _active_block
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
        self._last_emitted_tool_signature: dict[
            str, tuple[str, str, str, int | None]
        ] = {}  # bounded-accumulator-ok
        # S-13 (wt-028-display P1 / AC-02 / AC-03): cross-kind
        # identical-content dedup at the rendered-record seam. The
        # ``pi`` agent (and others) can emit a ``text:`` event and a
        # ``thinking:`` event with byte-identical bodies for the same
        # reasoning pass; tracking the last ``(event_kind, body)`` per
        # unit at the record seam drops the second entry only when the
        # event kind ALSO matches (a cross-kind correlated companion),
        # so two distinct identical tool_use events still produce two
        # record entries. The lifetime never exceeds the per-wave set
        # so the bounded-accumulator contract carries through.
        # per-unit; drained by drop_unit(unit_id) in the parallel coordinator finally
        self._last_recorded_body: dict[
            str, tuple[ActivityEventKind, str]
        ] = {}  # bounded-accumulator-ok: drop_unit
        # Recent parser aliases for one tool invocation. Pi can announce a
        # call through multiple wire events; retaining a small per-unit window
        # keeps that transport duplication off both presentation surfaces.
        self._recorded_tool_call_ids: dict[
            str, tuple[str, ...]
        ] = {}  # bounded-accumulator-ok: capped at 64 per unit
        self._last_tool_result_content: dict[
            str, tuple[str | None, str]
        ] = {}  # bounded-accumulator-ok: drop_unit
        # A terminal transport occasionally repeats one result record in a
        # tight burst. Hold one per unit until its run boundary is known.
        self._pending_tool_results: dict[
            str, tuple[str, dict[str, object], str | None, str | None, float, int]
        ] = {}  # bounded-accumulator-ok: one pending result per unit
        # DA-002 (wt-028-display S-2 / S-3): the streaming-block
        # live-log dedup. When a ``TEXT`` streaming block closes and
        # the next event opens a ``THINKING`` streaming block with
        # the same body (the cross-kind companion the ``pi`` agent
        # emits for one logical reasoning pass), the close-time live
        # print must also dedup so the live log and the rendered
        # record stay one entry per logical event -- the
        # ``_append_recorded_entry`` dedup covers the file surface
        # but the live console prints in ``_close_block`` BEFORE the
        # dedup check fires, so the live log would carry the
        # duplicate. The companion body is keyed on
        # ``(kind, body)`` per unit; the dedup only fires on a
        # cross-kind text/thinking pair with the same body, so two
        # distinct identical ``text`` events (the tool_use flood
        # case adapted for streaming) both keep their live entries.
        # per-unit; drained by drop_unit(unit_id) in the parallel coordinator finally
        self._last_text_thinking_block_close: dict[
            str, tuple[ActivityEventKind, str]
        ] = {}  # bounded-accumulator-ok: drop_unit

        # wt-028-display S-5 (AC-04): the most recent (phase, cycle,
        # iter_) seen per active unit, refreshed by
        # ``_emit_phase_header_record``. Ordinary activity events
        # read this in ``_append_recorded_entry`` so the rendered
        # record's ``phase`` / ``cycle`` / ``iter_`` fields are
        # populated instead of ``None``. Keyed per unit and
        # bounded by ``drop_unit`` (S-23).
        self._last_phase_per_unit: dict[
            str, tuple[str, int | None, str | None]
        ] = {}  # bounded-accumulator-ok: drop_unit

        self._workspace_root: Path = workspace_root if workspace_root is not None else Path.cwd()

        # Per-unit raw overflow logs, lazy-created on first oversized emit
        # Per-unit raw overflow logs, lazy-created on first oversized emit
        # per-unit; drained by drop_unit(unit_id) in the parallel coordinator finally
        self._overflow_logs: dict[str, RawOverflowLog] = {}  # bounded-accumulator-ok: drop_unit
        # Track units where the 50 MB guard WARN was already emitted
        self._overflow_warned: set[str] = set()  # bounded-accumulator-ok: drop_unit
        # Per-unit last drop-warning timestamp; _NEVER_WARNED means never warned yet
        self._drop_last_warned: dict[str, float] = {}  # bounded-accumulator-ok: drop_unit

        # P0 (wt-028-display S-11 / AC-07): the rendered-record writer is
        # the production seam that puts one entry per agent event under
        # ``.agent/raw/<safe_id>.rendered.log``. The dictionary is bounded
        # by ``drop_unit`` (parallel coordinator ``finally``) and by
        # ``stop()``'s flush, so a long unattended run cannot accumulate
        # writers beyond the per-wave set. Quiet mode (single-line runs)
        # and tests that disable the writer get a no-op append.
        # per-unit; drained by drop_unit(unit_id) in the parallel coordinator finally
        self._rendered_writers: dict[
            str, RenderedRecordWriter
        ] = {}  # bounded-accumulator-ok: drop_unit

        # S-15 (wt-028-display P1 / AC-05): phase banners may fire
        # before any unit has produced visible events (a phase_start
        # triggered by an empty session, or a phase banner emitted
        # above the first agent line). The render-to-record seam
        # only writes to existing writers, so a phase_start with no
        # writers would be lost. Buffer the headers here and flush
        # them when the first writer is created. The buffer is
        # bounded by the number of phase transitions per run, so
        # even an aggressive failure loop cannot accumulate
        # unbounded entries.
        self._pending_phase_headers: list[
            dict[str, object]
        ] = []  # bounded-accumulator-ok: _flush_pending_phase_headers

        self._activity_router: ActivityRouter = ActivityRouter(
            on_event=self._on_activity_router_event,
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
            # wt-047-stall-label (DA-001): an externally-supplied
            # subscriber owns its own constructor args; the display
            # cannot re-construct it with a sink. Bind the host's
            # :meth:`set_watchdog_attention` callback through the
            # subscriber's public late-binder so the STALLED slot
            # is populated for the injected-subscriber path too.
            # The constructor path below binds at construction
            # time (cheaper) so the binder is only needed here.
            self._subscriber.set_watchdog_attention_sink(self.set_watchdog_attention)
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
                watchdog_attention_sink=self.set_watchdog_attention,
            )

    @property
    def _console(self) -> Console:
        return self._ctx.console

    # -- Pure helpers (inlined from _PlainLogRendererBase) ----------------

    def _format_timestamp(self, ts: datetime) -> str:
        """Format a wall-clock datetime as ``HH:MM:SS`` for the line chrome.

        DA-002 (wt-028-display P1 / S-4 / AC-03): the line chrome
        column carries a compact ``HH:MM:SS`` token (8 chars) so the
        chrome fits on a 40-column terminal alongside the badge
        and at least one body token. Pre-fix, the chrome used the
        full ISO-8601 string (33 chars) which left zero room for
        the body on a 40-column terminal -- Rich truncated the
        line to the chrome and dropped the body / continuation
        badge. The full ISO-8601 timestamp still appears in the
        rendered record (see :mod:`ralph.display.record_writer`)
        so the file surface stays lossless; only the live-log
        chrome column is compacted.
        """
        return ts.strftime("%H:%M:%S")

    @staticmethod
    def _format_hh_mm_ss(ts: datetime) -> str:
        """Format a wall-clock datetime as ``HH:MM:SS`` for span markers.

        The sketch-J close-line shape uses a compact ``HH:MM:SS`` so the
        span ``start \u2192 end`` fits on one line; the full ISO-8601
        timestamp still appears as the leading column of the line
        (see ``_format_timestamp``). Time is rendered as-is from the
        ``datetime`` value (the production clock returns a UTC value).
        """
        return ts.strftime("%H:%M:%S")

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format a duration in seconds as a compact human string.

        Sub-minute deltas render as ``<n>s`` (e.g. ``0s``, ``10s``);
        longer deltas render as ``<m>m<ss>s`` (e.g. ``1m30s``). The
        value is floored to whole seconds so a sub-second
        monotonic delta still renders as ``0s``.
        """
        total = max(0, int(seconds))
        if total < _SECONDS_PER_MINUTE:
            return f"{total}s"
        minutes, secs = divmod(total, _SECONDS_PER_MINUTE)
        return f"{minutes}m{secs}s"

    def _build_line(
        self,
        timestamp: str,
        level: str,
        cat: str,
        suffix: str,
        *,
        leading_indent: str = "",
    ) -> Text:
        """Build a styled Text line with no LEVEL/CAT badge.

        wt-028-display S-4: the rendered line shape is
        ``HH:MM:SS [tag][unit] body`` -- no LEVEL or CAT
        plumbing-vocabulary badge in the chrome. Severity is
        carried by the renderer icon+label carrier. ``level``
        and ``cat`` parameters are kept (so existing call sites
        still typecheck) but rendered as nothing.
        """
        del level
        del cat
        t = Text()
        if leading_indent:
            t.append(leading_indent)
        t.append(timestamp + " ")
        t.append(suffix)
        return t

    @staticmethod
    def _wrap_body_with_hanging_indent(
        prefix: str,
        body: str,
        *,
        total_width: int,
        body_measure: int,
    ) -> str:
        """Wrap ``body`` so continuation lines hang-indent to ``prefix``.

        S-4 (wt-028-display P1 / AC-03 / DA-002) and S-5 (P1 / AC-04 /
        DA-004): one shared rendering seam applies two contracts:

        * the wide-terminal measure cap (``body_measure`` -- never
          wider than 100 cols on a 250-col terminal, never narrower
          than 40 cols),
        * the hanging-indent continuation that keeps the body
          aligned with the prefix column so the reader does not
          lose the line's structural position on a wrap.

        P0 (wt-028-display S-4 / DA-002): the body passed in is the
        raw body content, NOT the full chrome-prefixed rendered text.
        Passing the full rendered text would consume most of the
        narrow-terminal budget on the chrome prefix and force
        ``textwrap`` to break the body's natural words into
        one-character fragments (the pre-fix bug). The first line
        of the line carries the timestamp + level + cat + badge
        chrome separately; the body is wrapped as a standalone
        string at the ``prefix`` column, and the caller hangs
        continuations at ``prefix`` via the matching ``hang_prefix``
        in ``emit_activity_line``.

        DA-002 (S-4): no built-in ``subsequent_indent`` is emitted
        by ``textwrap.wrap`` here. The continuation print path in
        ``emit_activity_line`` prefixes every continuation line with
        the matching ``hang_prefix``, so a built-in indent would
        double-count (the pre-fix bug doubled the indent at 40 cols).

        The available column count for the body is measured in terminal display
        cells: ``min(total_width - cell_len(prefix), body_measure -
        cell_len(prefix))``. This keeps wide and combining Unicode from shifting
        the continuation column. A single token wider than the
        budget still wraps without crashing on
        a zero-or-negative effective measure. A slightly over-budget identifier
        stays intact on its own row, while a substantially over-budget token folds
        to preserve its recovery tail. Rules, tables, and
        aligned columns that need the full terminal width go
        through a different emit path (``_console.print`` with
        ``no_wrap=True``) so this helper does not touch them.

        Returns the body ready to be appended after ``prefix``: the
        first line carries the original ``body`` text, continuation
        lines are NOT prefixed with any indent here -- the caller
        applies the matching hang prefix when emitting. When the
        body fits in one line the original string is returned
        unchanged so the single-line case has no trailing
        whitespace.
        """
        if not body:
            return body
        prefix_width = cell_len(prefix)
        # Cap by both terminal width and the body measure; both subtract the
        # display-cell width of the prefix so wide/combining chrome cannot push
        # the body beyond the right edge on a short terminal.
        budget_terminal = total_width - prefix_width
        budget_measure = body_measure - prefix_width
        # DA-004 (S-4): the floor is the available budget itself, not a
        # fixed ``max(20, ...)``. On a 40-column terminal with a 33-col
        # chrome prefix the available body width is 7 cols; the previous
        # ``max(20, ...)`` floor forced body wraps to 20 cols that
        # overflowed the terminal and broke the hanging-indent column.
        # A body that fits a single token flows through unchanged.
        budget = max(1, min(budget_terminal, budget_measure))
        if budget <= 0 or cell_len(body) <= budget:
            return body

        def split_to_cells(value: str) -> list[str]:
            """Fold an unbroken value without cutting a display cell budget."""
            chunks: list[str] = []
            chunk = ""
            for character in value:
                candidate = chunk + character
                if chunk and cell_len(candidate) > budget:
                    chunks.append(chunk)
                    chunk = character
                else:
                    chunk = candidate
            if chunk:
                chunks.append(chunk)
            return chunks or [value]

        def wrap_line(line: str) -> list[str]:
            if not line:
                return [line]
            rows: list[str] = []
            row = ""
            for token in line.split():
                candidate = token if not row else f"{row} {token}"
                if row and cell_len(candidate) > budget:
                    rows.append(row)
                    row = ""
                # Preserve slightly over-budget identifiers, but fold a much
                # longer token so no recovery-relevant suffix is clipped.
                if cell_len(token) > budget * 2:
                    rows.extend(split_to_cells(token))
                else:
                    row = token if not row else candidate
            if row:
                rows.append(row)
            return rows or [line]

        return "\n".join(chunk for line in body.split("\n") for chunk in wrap_line(line))

    @staticmethod
    def _build_agents_parts(orientation: RunStartOrientation) -> list[str]:
        """Collect developer agent+model tokens for the run-start agents line."""
        parts: list[str] = []
        if orientation.developer_agent is not None:
            parts.append(f"developer={strip_markup(orientation.developer_agent)}")
        if orientation.developer_model is not None:
            parts.append(f"model={strip_markup(orientation.developer_model)}")
        return parts

    @classmethod
    def strip_markup(cls, line: str) -> str:
        """Strip valid Rich markup and terminal control sequences.

        Malformed markup remains literal so agent output is not lost.
        """
        line = _strip_markup(line)
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
        body_text: str | None = None,
        source_timestamp: str | None = None,
    ) -> None:
        """Emit a kind-tagged, level-badged content line.

        ``body_text`` (wt-028-display S-4 / DA-002): the body content
        to wrap on continuations. When ``None`` (the default) the
        function falls back to ``sanitized`` (the full rendered
        text) -- the pre-fix behavior that broke the 40-column wrap
        by consuming the budget on the chrome prefix. When a caller
        passes the raw body separately, the wrap uses it as a
        standalone string and the first line keeps the chrome prefix
        (timestamp + level + cat + badge + rendered chrome) on its
        own row, so a continuation at the floor carries readable
        multiword body chunks instead of one-character fragments.

        ``source_timestamp`` (DA-003 / wt-028-display): the
        source-event ISO-8601 timestamp the parser pipeline
        extracted from the agent output. When supplied, it
        replaces the display clock fallback so the rendered
        record carries the source time instead of the moment
        ``emit_activity_line`` happened to run. ``None`` (the
        default) keeps the pre-fix behaviour -- the display
        clock stamps the line.
        """
        if options is None:
            options = _ActivityLineOptions(
                condensed_ref=condensed_ref,
                condensed_flag=condensed_flag,
                summary_line=summary_line,
                ai_summary_line=ai_summary_line,
                tool_signature=tool_signature,
            )
        opts = options
        # DA-003 (wt-028-display): when the caller supplied a
        # source-event ISO-8601 timestamp, use it; otherwise stamp
        # the display clock. The chrome column reads the same
        # ``HH:MM:SS`` token either way so the visual contract
        # is unchanged; only the rendered record carries the
        # source time end-to-end when the caller had one.
        if source_timestamp is not None:
            timestamp = _format_iso_timestamp_hh_mm_ss(source_timestamp)
        else:
            timestamp = self._format_timestamp(self._clock())
        rendered_unit_id = _render_unit_id(unit_id)
        base_tag = _KIND_TO_TAG.get(kind, "content")
        level = _KIND_TO_LEVEL.get(kind, "INFO")
        cat = TAG_CATEGORY.get(base_tag, "META")
        # raw kind is the transcript/log sink path: preserve literal markup
        # so copy-pasteable raw payloads survive verbatim. Other kinds render
        # to the visible console and reduce valid Rich markup to plain text.
        sanitized = _sanitize(content) if kind == "raw" else strip_markup(content)
        if opts.condensed_ref is not None and opts.condensed_flag:
            sanitized = f"{sanitized} [see {opts.condensed_ref}]"

        # S-7 (wt-028-display P1): streaming blocks buffer fragments and the
        # close path emits the single coalesced entry. ``_route_streaming``
        # returns False when the live console should stay silent.
        if self._route_streaming(unit_id, kind, content, base_tag, timestamp) is False:
            return

        # S-14 (wt-028-display P1 / AC-04): the empty-body guard
        # for non-streaming TEXT / THINKING / ERROR / TOOL_RESULT
        # kinds. Empty bodies produce no live-log line AND no
        # record entry; the seam append below short-circuits the
        # same way. Streaming TEXT / THINKING already had its own
        # ``content.strip()`` guard at the top of the
        # ``_STREAMING_KINDS`` branch; ``tool_use`` / ``status`` /
        # ``raw`` / ``progress`` are not body-bearing lines and
        # may legitimately carry no body, so they are excluded.
        if (
            kind in {"text", "thinking", "error", "tool_result"}
            and not sanitized.strip()
            and opts.record_body is None
        ):
            return

        if kind == "tool_use" and opts.tool_signature is not None:
            tool_name, tool_path = opts.tool_signature
            metadata = opts.activity_metadata or {}
            input_obj = metadata.get("input", metadata.get("args"))
            input_dict = cast("dict[str, object]", input_obj) if isinstance(input_obj, dict) else {}
            pattern = input_dict.get("pattern", metadata.get("pattern", ""))
            line_start = input_dict.get("line_start")
            self._last_emitted_tool_signature[unit_id] = (
                tool_name,
                tool_path,
                str(pattern or ""),
                line_start
                if isinstance(line_start, int) and not isinstance(line_start, bool)
                else None,
            )

        self._emit_activity_supplements(unit_id, timestamp, base_tag, cat, opts)

        # S-7 (wt-028-display P1 / AC-07): quiet mode suppresses the
        # terminal surface only. The rendered record append below
        # still runs so the file surface keeps the same presented
        # entries a non-quiet run would have written.
        if not self._is_quiet:
            # DA-002 / DA-004 (S-4 + S-5): the live log body hangs at
            # the prefix column on wrap and is capped at the shared
            # ``body_measure`` so a 250-col terminal doesn't print
            # 180-char lines. We split the work into two print calls:
            # the first prints the full badge-bearing line; the
            # subsequent continuations carry the chrome prefix and a
            # hanging indent that lands at the actual first body
            # column (timestamp + level + cat + badge prefix).
            # ``no_wrap=True`` prevents Rich from reflowing the
            # already-wrapped first line (the pre-fix bug dropped
            # the chrome into one or more column-0 rows on a 40-col
            # console and put the body on a continuation row that
            # Rich re-wrapped to column 0).
            #
            # DA-004 (S-4): the hanging indent must mirror the FULL
            # chrome prefix emitted on the first line -- timestamp,
            # level, category, badge -- so the body continuation
            # lands at the same column the first-line body started
            # at. Pre-fix, the hang only covered the badge prefix;
            # on a 40-column terminal where Rich folds the
            # timestamp/level/cat, the first body chunk landed at
            # column 0 while continuations started at the badge
            # column, breaking the structural column the body
            # belonged to.
            # wt-028-display S-4: the chrome prefix is now just
            # ``HH:MM:SS `` -- no LEVEL or CAT badge. Severity
            # is carried by the renderer's own icon+label carrier
            # (e.g. ``✓ PASS`` / ``✗ FAIL``) which already
            # survives color-off, so the second copy of severity
            # in the chrome prefix was the duplication AC-03
            # explicitly forbids. The plumbing vocabulary
            # (``META``/``OUT``) never reaches the operator
            # surface.
            chrome_prefix = f"{timestamp} "
            display_tag = {"content": "output", "think": "reasoning"}.get(base_tag, base_tag)
            badge_prefix = f"[{display_tag}][{rendered_unit_id}] "
            # DA-002 (S-4 / S-12 / AC-07): the canonical
            # ``PresentedEntry`` hierarchy data drives the live log's
            # hanging-indent continuation column. ``indent_level``
            # adds N copies of the per-level indent (``_INDENT_WIDTH``
            # spaces) so a tool_result hangs one level under its
            # call, and a reasoning entry reads as one subordinated
            # passage. The badge column stays at the same physical
            # column for level-0 entries so a level-1 continuation
            # visibly nests under the call it belongs to.
            indent_level: int = max(0, int(cast("int", getattr(opts, "indent_level", 0))))
            # ``grouping_role`` is read off ``opts`` so downstream
            # consumers (downstream greps, screen readers, the
            # audit trail) can recover the structural position; the
            # live log surface only needs the indent column. The
            # read here is the pinned structural-position probe
            # that the DA-002 / AC-07 contract depends on.
            grouping_role: str = str(opts.grouping_role) if opts.grouping_role else "agent_text"
            del grouping_role
            level_indent = " " * (_INDENT_WIDTH * indent_level)
            # The level indent prefixes the chrome column on the
            # first line and the continuation column on wrap
            # rows, so a level-1 entry starts at column
            # ``_INDENT_WIDTH`` and continuations line up
            # vertically under the badge column of the first
            # line. ``chrome_prefix`` is the original
            # timestamp/level/cat text -- the leading indent is
            # applied via ``_build_line(leading_indent=...)`` and
            # via the leading-spaces in ``hang_prefix``.
            # DA-002 (S-4): the hang column is the FIRST body
            # token's column on the first line, which equals
            # the leading_indent + the chrome_prefix length +
            # the badge_prefix length (because the body itself
            # starts immediately after the badge). On a 40-col
            # console the chrome (18) + badge (14) consumes 32
            # cols, leaving only 8 cols for the body on the
            # first line; the manual wrap must use the SAME
            # budget so the first body token column equals the
            # hang column on every continuation.
            full_chrome_prefix = chrome_prefix + badge_prefix
            # DA-002 (S-4): the wrap budget must subtract the
            # LEVEL indent too, because the first line is
            # ``level_indent + chrome + badge + first_chunk`` --
            # ignoring the level indent caused the wrapped
            # first line to overshoot the terminal width and
            # Rich truncated the body (the pre-fix bug on
            # level-1 entries like ``[result]``).
            effective_prefix_for_wrap = level_indent + full_chrome_prefix
            # Every physical transcript row must remain independently greppable.
            # Repeat the source timestamp plus category and unit carrier rather
            # than replacing them with whitespace on structured continuations.
            # The repeated prefix also keeps the continuation body in its stable
            # grid column without relying on surrounding context.
            hang_prefix = level_indent + full_chrome_prefix
            # DA-002 (S-4): wrap the body against the FULL
            # chrome+badge prefix so the first body token column
            # on the first line equals the hang column on every
            # continuation. Pre-fix, the wrap budget was computed
            # against ``badge_prefix`` only (14 chars), but the
            # actual first-line chrome is 32 chars long; Rich then
            # re-wrapped the resulting 32 + 26 = 58-char first
            # line at 40 cols, dropping continuations to column 0.
            wrap_target = body_text if body_text is not None else sanitized
            # A tool result is a single transaction outcome, not prose: keep
            # it on one physical row whenever the terminal itself permits it.
            # Other activity still uses the readable body-measure cap.
            wrap_measure = self._ctx.width if kind == "tool_result" else self._ctx.body_measure()
            wrapped_body = self._wrap_body_with_hanging_indent(
                effective_prefix_for_wrap,
                wrap_target,
                total_width=self._ctx.width,
                body_measure=wrap_measure,
            )
            chunks = wrapped_body.split("\n")
            first_chunk = chunks[0]
            self._console.print(
                self._build_line(
                    timestamp,
                    level,
                    cat,
                    f"{badge_prefix}{first_chunk}",
                    leading_indent=level_indent,
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
                overflow="ignore",
            )
            for chunk in chunks[1:]:
                # ponytail: continuations keep the badge prefix so
                # the line still reads as a continuation of the same
                # entry; the timestamp/level/cat is dropped because
                # the badge carries the structural information and
                # the reader can scroll back to the first line for
                # the timestamp. ``grouping_role`` is carried
                # alongside the badge column so a downstream grep
                # can recover the structural position even when the
                # leading whitespace is stripped (e.g. a screen
                # reader or a braille display). ``no_wrap=True`` so
                # Rich cannot re-wrap our already-wrapped continuation
                # (the pre-fix bug dropped continuations to column 0
                # on a 40-col console).
                continuation = f"{hang_prefix}{chunk}"
                if kind == "tool_use":
                    continuation += " ↳"
                self._console.print(
                    continuation,
                    markup=False,
                    highlight=False,
                    no_wrap=True,
                    overflow="ignore",
                )

        self._append_seam_record(unit_id, kind, sanitized, timestamp, opts)

    def _route_streaming(
        self,
        unit_id: str,
        kind: str,
        content: str,
        base_tag: str,
        timestamp: str,
    ) -> bool:
        """Dispatch streaming blocks; return False to suppress live emission."""
        if kind not in _STREAMING_KINDS:
            for uid in list(self._active_block.keys()):
                self._close_block(uid, timestamp)
            self._update_counters(kind, is_new_block=False)
            return True

        if kind == "thinking" and not content.strip():
            return False

        block_tags = _STREAMING_BLOCK_TAGS.get(base_tag)
        if block_tags is None:
            self._update_counters(kind, is_new_block=False)
            return True

        ctx = _StreamingCtx(
            unit_id=unit_id,
            kind=kind,
            content=content,
            base_tag=base_tag,
            timestamp=timestamp,
        )
        self._process_streaming_block(ctx, block_tags)
        return False

    def _append_seam_record(
        self,
        unit_id: str,
        kind: str,
        sanitized: str,
        timestamp: str,
        opts: _ActivityLineOptions,
    ) -> None:
        """Append one record entry at the shared presentation seam (S-13)."""
        if kind == "raw":
            return
        if kind == ActivityEventKind.SUBAGENT_PROGRESS:
            return
        try:
            event_kind = ActivityEventKind(kind)
        except ValueError:
            # Defensive: unrecognised kinds still reach the live log but skip
            # the record append rather than crash the seam.
            return
        record_body = opts.record_body or sanitized
        self._append_recorded_entry(
            unit_id,
            event_kind=event_kind,
            body=record_body,
            timestamp=timestamp,
            metadata=opts.activity_metadata,
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
        """Close an active streaming block: emit exactly one coalesced entry.

        S-7 (wt-028-display P1): one entry per block. The close line carries
        the joined passage with span and duration (sketch J shape). The
        preview line, the AI summary line, the close summary, and the
        per-fragment emission paths are all retired; the live log must
        present a single coalesced entry for the whole block rather than
        the up-to-four repeat-renderings that used to surface here.

        S-13 (wt-028-display P1): the close line is shaped as
        ``\u22ef <tag> \u00b7 <start> \u2192 <end> \u00b7 <duration>`` followed by the
        joined passage on the next line. ``<start>`` is the wall-clock
        time the block opened (``HH:MM:SS``); ``<end>`` is the wall-clock
        time of the close; ``<duration>`` is the monotonic delta in
        seconds. Both clocks are injected, so tests render a deterministic
        ``0s`` / ``10s`` / ``<m>m<ss>s`` shape.
        """
        if unit_id not in self._active_block:
            return
        rendered_unit_id = _render_unit_id(unit_id)
        base_tag, accumulated = self._active_block.pop(unit_id)
        self._last_checkpoint_chars.pop(unit_id, None)
        self._active_block_chars.pop(unit_id, None)
        # S-13: pop the recorded block-open wall + monotonic times so the
        # close line can carry the sanctioned sketch-J span and duration.
        open_wall = self._block_open_wall.pop(unit_id, None)
        open_mono = self._block_open_mono.pop(unit_id, None)
        end_wall = self._clock()
        end_mono = self._monotonic()
        block_tags = _STREAMING_BLOCK_TAGS.get(base_tag)
        if block_tags is None:
            return
        if not accumulated:
            return
        joined = " ".join(accumulated)
        sanitized_joined = _sanitize(joined)
        # S-7 / AC-06: condensation still applies to the joined passage --
        # a long reasoning or text block may exceed the soft limit on
        # close. The condenser carries the count + size + destination
        # marker the product criteria require; the verbatim overflow
        # log under .agent/raw/<safe_id>.log remains the destination.
        overflow = self._get_overflow_log(unit_id)
        overflow_ref = overflow.relative_reference(self._workspace_root)
        if base_tag == "think":
            from ralph.display.presented_entry import _strip_markdown_emphasis

            sanitized_joined = _strip_markdown_emphasis(sanitized_joined)
        visible, condensed_flag = cast(
            "tuple[str, bool]",
            condense_content(
                sanitized_joined,
                options=CondenseOptions(
                    soft_limit=self._ctx.condenser_soft_limit,
                    hard_limit=self._ctx.condenser_hard_limit,
                    overflow_ref=overflow_ref,
                ),
            ),
        )
        if condensed_flag:
            with contextlib.suppress(Exception):
                overflow.append(sanitized_joined)
                self._check_overflow_size(unit_id, overflow)
        # S-7 / S-9 / S-13 (wt-028-display P1 / AC-04 / AC-05): single-entry
        # shape with human vocabulary only. The "fragments" footer
        # and the "CONT" category are machine vocabulary and
        # belong on no surface. The close line carries the base
        # tag, a span and duration suffix, and (when oversized) the
        # condensation marker the condenser already produced; the
        # verbatim overflow log under .agent/raw/<safe_id>.log
        # remains the destination the marker points to.
        # DA-002 (wt-028-display S-2 / S-3): the cross-kind text/
        # thinking companion dedup at close time. When the previous
        # closed block for this unit was a TEXT streaming block
        # whose visible body equals THIS block's visible body (and
        # the kind differs -- a THINKING companion), the pre-fix
        # contract fired the live console print here and the record
        # append below, leaving the live log with two entries for
        # one logical reasoning pass even though the record already
        # dedup'd at ``_append_recorded_entry``. The dedup runs
        # BEFORE the live print and the record append so the live
        # log and the rendered record stay one entry per logical
        # event. State is left unchanged when a dedup fires so a
        # third identical event still dedups against the original
        # (the tool_use flood contract adapted for streaming).
        _base_tag_to_record_kind = {
            "content": ActivityEventKind.TEXT,
            "think": ActivityEventKind.THINKING,
        }
        close_record_kind = _base_tag_to_record_kind.get(base_tag, ActivityEventKind.TEXT)
        if (
            close_record_kind in (ActivityEventKind.TEXT, ActivityEventKind.THINKING)
            and visible.strip()
        ):
            prev_close = self._last_text_thinking_block_close.get(unit_id)
            if (
                prev_close is not None
                and prev_close[1] == visible
                and prev_close[0] in (ActivityEventKind.TEXT, ActivityEventKind.THINKING)
                and prev_close[0] != close_record_kind
            ):
                # Drop the live print and the record append; the
                # previous entry already represents this logical
                # reasoning pass. State is left unchanged so a
                # follow-up identical event still dedups against
                # the SAME previous entry.
                return
        start_str = self._format_hh_mm_ss(open_wall) if open_wall is not None else "??:??:??"
        end_str = self._format_hh_mm_ss(end_wall)
        if open_mono is not None:
            duration_str = self._format_duration(end_mono - open_mono)
        else:
            duration_str = "0s"
        display_tag = {"content": "output", "think": "reasoning"}.get(base_tag, base_tag)
        body = f"\u22ef {display_tag} \u00b7 {start_str} \u2192 {end_str} \u00b7 {duration_str}\n{visible}"
        # S-7 (AC-07): quiet mode suppresses the terminal surface;
        # the record append below still runs so the file surface
        # keeps the same close entry.
        if not self._is_quiet:
            # DA-002 / DA-004 (S-4 + S-5): the close-entry body has
            # the span header on its own line then the joined
            # passage. Wrap each line independently so a wide
            # console stays at the body_measure cap and a narrow
            # console's continuations hang at the badge column.
            # ``no_wrap=True`` prevents Rich from reflowing our
            # manually wrapped lines (the pre-fix bug dropped
            # continuations to column 0 on a 40-col console because
            # Rich re-wrapped the ``close_hang_prefix + wrapped_cont``
            # embedded-newline string and only the first embedded
            # line carried the prefix).
            close_badge_prefix = f"[{display_tag}][{rendered_unit_id}] "
            close_hang_prefix = " " * cell_len(close_badge_prefix)
            body_chunks = body.split("\n")
            wrapped_first = self._wrap_body_with_hanging_indent(
                close_badge_prefix,
                body_chunks[0],
                total_width=self._ctx.width,
                body_measure=self._ctx.body_measure(),
            )
            self._console.print(
                self._build_line(
                    timestamp,
                    "INFO",
                    "",
                    f"{close_badge_prefix}{wrapped_first}",
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
                overflow="ignore",
            )
            for continuation in body_chunks[1:]:
                wrapped_cont = self._wrap_body_with_hanging_indent(
                    close_badge_prefix,
                    continuation,
                    total_width=self._ctx.width,
                    body_measure=self._ctx.body_measure(),
                )
                # Each embedded line in ``wrapped_cont`` must
                # carry the hang prefix so the body stays at the
                # badge column on wrap. Print each line as its
                # own console.write so the prefix lands on every
                # row, not just the first (the pre-fix bug).
                for cont_line in wrapped_cont.split("\n"):
                    self._console.print(
                        f"{close_hang_prefix}{cont_line}",
                        markup=False,
                        highlight=False,
                        no_wrap=True,
                        overflow="ignore",
                    )

        # S-13 (wt-028-display P1 / AC-02 / AC-03): the close entry is
        # also the single record entry for the streaming block. Map the
        # base_tag back to an ``ActivityEventKind`` so the record line
        # carries the same kind vocabulary as the live log.
        _base_tag_to_kind = {
            "content": ActivityEventKind.TEXT,
            # wt-028-display S-3 (DA-001): the public base tag is
            # ``think``; the close path must follow the same key so
            # the record line carries the correct kind.
            "think": ActivityEventKind.THINKING,
        }
        record_kind = _base_tag_to_kind.get(base_tag, ActivityEventKind.TEXT)
        self._append_recorded_entry(
            unit_id,
            event_kind=record_kind,
            body=body,
            timestamp=timestamp,
        )
        # DA-002 (wt-028-display S-2 / S-3): record the close on
        # the per-unit live-log dedup key so a follow-up
        # cross-kind text/thinking companion dedups against THIS
        # block on the next close. Only set when the close actually
        # surfaced on at least one surface; the dedup at the top
        # of this function returns early before this point when the
        # live print and record append are both suppressed.
        if record_kind in (ActivityEventKind.TEXT, ActivityEventKind.THINKING):
            self._last_text_thinking_block_close[unit_id] = (record_kind, visible)

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
    ) -> tuple[str, str | None] | None:
        """Open a new streaming block: accumulate first fragment, suppress emission.

        S-7 (wt-028-display P1): one entry per block. The streaming layer is
        silent on open; the close path emits the single coalesced entry.
        Returning ``None`` here causes ``emit_activity_line`` to skip the
        per-fragment console-print while the fragment is still buffered
        into ``_active_block`` for ``_close_block`` to join.

        S-13 (wt-028-display P1): record block-open wall + monotonic times
        so the close path can render the sanctioned sketch-J span
        ``start → end`` and duration ``<n>s`` / ``<m>s<n>s`` shape. Both
        clocks flow through the injected ``self._clock`` and
        ``self._monotonic`` so tests stay deterministic.
        """
        self._active_block[ctx.unit_id] = (ctx.base_tag, [ctx.content])
        self._last_checkpoint_chars[ctx.unit_id] = 0
        self._active_block_chars[ctx.unit_id] = len(ctx.content)
        self._block_open_wall[ctx.unit_id] = self._clock()
        self._block_open_mono[ctx.unit_id] = self._monotonic()
        self._update_counters(ctx.kind, is_new_block=True)
        return None  # S-7: suppress per-fragment emission; close emits one entry

    def _continue_streaming_block(
        self,
        ctx: _StreamingCtx,
        accumulated: list[str],
        continue_tag: str,
        start_tag: str,
    ) -> tuple[str, str | None] | None:
        """Continue an existing streaming block: accumulate fragment, suppress emission.

        S-7 (wt-028-display P1): the live log is silent during streaming.
        The single coalesced entry is emitted on ``_close_block``.
        Returns ``None`` to suppress the per-fragment console-print while
        still appending to ``accumulated`` so the close path can join
        the passage. The close-and-reopen cap (memory-perf GAP-MEM-03)
        still fires here so a chatty stream cannot blow the fragment cap.
        """
        if self._ctx.streaming_dedup_enabled and accumulated and accumulated[-1] == ctx.content:
            return None
        if len(accumulated) >= _MAX_STREAMING_FRAGMENTS:
            # Close-and-reopen: close the current block (which emits the
            # coalesced entry) and open a fresh one with the current
            # fragment. Both paths are silent at the streaming layer; the
            # close is the single visible emission.
            self._close_block(ctx.unit_id, ctx.timestamp)
            self._handle_new_streaming_block(ctx, start_tag)
            return None  # S-7: suppress this fragment's per-line emission
        accumulated.append(ctx.content)
        running_total = self._active_block_chars.get(ctx.unit_id, 0) + len(ctx.content)
        self._active_block_chars[ctx.unit_id] = running_total
        return None  # S-7: suppress per-fragment emission; close emits one entry

    def _process_streaming_block(
        self,
        ctx: _StreamingCtx,
        block_tags: tuple[str, str],
    ) -> tuple[str, str | None] | None:
        """Dispatch streaming block state; returns (tag, override) or None on dedup/early-return."""
        start_tag, continue_tag = block_tags
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
                last_tool, last_path, _last_pattern, _last_line_start = tool_sig
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

    @property
    def run_started_monotonic(self) -> float | None:
        """Return the run-start monotonic anchor (``time.monotonic`` units) or ``None``.

        P0 (wt-028-display AC-01): exposed so the Status Bar can
        recompute elapsed at render time without a model re-push. The
        anchor is the same ``self._run_start_time`` value
        ``run_elapsed_seconds`` is built on, so callers that want a
        snapshot use the latter and callers that want the live bar to
        tick use this anchor plus ``self._monotonic()`` (or an
        injected clock) at render time.
        """
        return self._run_start_time

    @property
    def watchdog_attention(self) -> str | None:
        """Return the watchdog-sourced attention state, or ``None``.

        wt-047-stall-label: this is the Status Bar host's read of the
        watchdog's per-event stall assessment stream. The subscriber mirrors
        each assessment as ``"stalled"`` / ``None`` and calls
        :meth:`set_watchdog_attention`. The host substitutes the value only
        when pushed ``attention`` is None; pushed ``waiting`` / ``retrying`` /
        ``terminated`` always win. Returns ``None`` when no stall transition
        has been published since the last run cleanup.
        """
        with self._watchdog_attention_lock:
            return self._watchdog_attention

    def set_watchdog_attention(self, value: str | None) -> None:
        """Set the watchdog-sourced attention state.

        wt-047-stall-label: the subscriber's sink mirrors every watchdog
        event's authoritative assessment as ``"stalled"`` / ``None``.
        It is called from the subscriber thread (indirectly from the watchdog
        emit path), so the dedicated lock keeps Status Bar reads race-free.
        ``None`` clears the stall.

        # ponytail: one run-level slot is last-writer-wins during fan-out;
        # track per-unit stalls only if concurrent stalls become an operator hazard.
        """
        with self._watchdog_attention_lock:
            self._watchdog_attention = value

    def _get_overflow_log(self, unit_id: str) -> RawOverflowLog:
        if unit_id not in self._overflow_logs:
            self._overflow_logs[unit_id] = RawOverflowLog(
                self._workspace_root, unit_id, max_bytes=_MAX_OVERFLOW_FILE_BYTES
            )
        return self._overflow_logs[unit_id]

    def _result_preview_target(self, unit_id: str, metadata: dict[str, object]) -> tuple[str, str]:
        """Return a correlated result tool name/path, with display-local fallback."""
        tool_name = str(metadata.get("tool_name", "") or "")
        path = str(metadata.get("tool_path", "") or "")
        previous = self._last_emitted_tool_signature.get(unit_id)
        if previous is not None:
            tool_name = tool_name or previous[0]
            path = path or previous[1]
        return tool_name.removeprefix("mcp__ralph__").removeprefix("ralph."), path

    def _result_preview_input(
        self, unit_id: str, metadata: dict[str, object], content: str
    ) -> tuple[str, dict[str, object], bool]:
        """Build a correlated result envelope and report whether it is previewable."""
        tool_name, path = self._result_preview_target(unit_id, metadata)
        previous = self._last_emitted_tool_signature.get(unit_id)
        correlated_start = previous[3] if previous is not None else None
        result_start = metadata.get("line_start")
        result_content: object = content
        if tool_name == "read_file":
            with contextlib.suppress(ValueError):
                envelope = cast("object", json.loads(content))
                if isinstance(envelope, dict):
                    envelope_path = envelope.get("path")
                    if isinstance(envelope_path, str) and envelope_path:
                        path = envelope_path
                    envelope_content = envelope.get("content")
                    if isinstance(envelope_content, str):
                        result_content = envelope_content
                    else:
                        return tool_name, {"input": {}}, False
                else:
                    return tool_name, {"input": {}}, False
        payload: dict[str, object] = {
            "path": path,
            "content": result_content,
            "line_start": result_start if isinstance(result_start, int) else correlated_start or 1,
            "is_snippet": bool(result_start is None and correlated_start is None),
        }
        preview_input: dict[str, object] = {"input": payload}
        if tool_name in {"grep_files", "search_files"}:
            previous = self._last_emitted_tool_signature.get(unit_id)
            if previous is not None:
                payload["pattern"] = previous[2]
        return (
            tool_name,
            preview_input,
            payload_from_tool_event(tool_name, preview_input) is not None,
        )

    def _emit_activity_preview(
        self,
        unit_id: str,
        kind: ActivityEventKind,
        tool_name: str,
        preview_input: dict[str, object],
        timestamp: str,
        *,
        include_header: bool = True,
    ) -> None:
        """Print a tool-use or recognized successful result preview."""
        self._emit_file_preview(
            unit_id, kind, tool_name, preview_input, timestamp, include_header=include_header
        )

    def _emit_file_preview(
        self,
        unit_id: str,
        kind: ActivityEventKind,
        tool_name: str,
        preview_input: dict[str, object],
        timestamp: str,
        *,
        include_header: bool = True,
    ) -> None:
        """Project a file preview to the record and, unless quiet, terminal."""
        overflow = self._get_overflow_log(unit_id)
        overflow_ref = overflow.relative_reference(self._workspace_root)
        _record_preview, full_source = preview_record_text(
            tool_name,
            preview_input,
            overflow_ref=overflow_ref,
            glyphs_enabled=self._ctx.glyphs_enabled,
        )
        if full_source is not None and full_source.count("\n") + 1 > _PREVIEW_MAX_LINES:
            overflow.append(full_source)
            self._check_overflow_size(unit_id, overflow)
        if self._is_quiet:
            return
        preview = build_edit_preview(
            tool_name,
            preview_input,
            width=self._ctx.width,
            terminal_bg_is_light=self._terminal_bg_is_light,
            overflow_ref=overflow_ref,
            glyphs_enabled=self._ctx.glyphs_enabled,
            diff_fills=diff_fill_styles(self._terminal_bg_is_light),
        )
        if preview is None:
            return
        path = ""
        payload = preview_input.get("input")
        if isinstance(payload, dict):
            path_value = payload.get("path", "")
            path = path_value if isinstance(path_value, str) else ""
        with contextlib.suppress(Exception):
            preview_body = Padding(preview, (0, 0, 0, _INDENT_WIDTH * 2))
            if include_header:
                self._console.print(
                    Group(
                        Padding(
                            preview_header(
                                tool_name,
                                path or "artifact",
                                glyphs_enabled=self._ctx.glyphs_enabled,
                            ),
                            (0, 0, 0, _INDENT_WIDTH),
                        ),
                        preview_body,
                    )
                )
            else:
                self._console.print(preview_body)
        del kind, timestamp

    def _get_rendered_writer(self, unit_id: str) -> RenderedRecordWriter | None:
        """Return the per-unit rendered-record writer, lazy-created.

        P0 (wt-028-display S-11 / AC-07): the writer is created on
        first use so quiet-mode runs and tests that never emit
        activity events pay nothing. ``drop_unit`` flushes and
        removes the writer; ``stop()`` flushes any straggler
        writers at run end so a buffered line is not lost.

        S-7 (wt-028-display P1 / AC-07): quiet mode no longer
        suppresses the file surface. The terminal surface is silent
        (``_is_quiet`` still short-circuits ``emit_activity_line``
        and the phase-banner methods), but the rendered record
        receives the same presented entries a non-quiet run would
        have written. Plumbing commands are unaffected because
        they never reach ``_emit_activity_event`` (they emit status
        and progress through their own sinks).
        """
        if unit_id not in self._rendered_writers:
            self._rendered_writers[unit_id] = RenderedRecordWriter(self._workspace_root, unit_id)
            # S-15 (AC-05): a phase_start may have arrived before any
            # unit produced a visible event. Flush the buffered
            # headers into the freshly-spawned writer so the
            # rendered record carries the phase boundary at the
            # right place.
            self._flush_pending_phase_headers(unit_id)
        return self._rendered_writers[unit_id]

    def _flush_pending_phase_headers(self, unit_id: str) -> None:
        """Drain buffered phase-start headers into ``unit_id``'s record.

        The buffer holds entries whose phase_start happened before
        any writer existed; once the first writer is created, the
        headers are flushed in arrival order so the rendered record
        shows the phase boundary at the correct position relative to
        the first event. The buffer is always drained (not partially
        flushed) so a stale header cannot leak into a later wave.
        """
        if not self._pending_phase_headers:
            return
        for entry in self._pending_phase_headers:
            cycle_value = entry["cycle"]
            cycle_int = int(cycle_value) if isinstance(cycle_value, int) else None
            phase = str(entry["phase"]) if entry["phase"] is not None else None
            iter_ = str(entry["iter_"]) if entry["iter_"] is not None else None
            self._append_recorded_entry(
                unit_id,
                event_kind=ActivityEventKind.LIFECYCLE,
                body=str(entry["body"]),
                timestamp=str(entry["timestamp"]),
                phase=phase,
                cycle=cycle_int,
                iter_=iter_,
            )
            if phase is not None:
                self._last_phase_per_unit[unit_id] = (phase, cycle_int, iter_)
        self._pending_phase_headers.clear()

    def _append_recorded_entry(
        self,
        unit_id: str,
        *,
        event_kind: ActivityEventKind,
        body: str,
        timestamp: str,
        metadata: dict[str, object] | None = None,
        phase: str | None = None,
        cycle: int | None = None,
        iter_: str | None = None,
    ) -> None:
        """Append a presented entry to the rendered record at the seam.

        S-13 (wt-028-display P1 / AC-02 / AC-03): the rendered record
        consumes the same stream the live log emits so the file
        surface and the terminal surface present one entry per
        logical event, in the same order, with the same vocabulary.
        The caller is responsible for stamping the entry with the
        display clock (``self._clock()``); the record line carries a
        real ``[hh:mm:ss]`` slot, never ``[??:??:??]``.

        Cross-kind identical-content dedup: a second entry for the
        same unit with a byte-identical body to the immediately
        previous entry is dropped on the file surface. This kills
        the ``text:`` / ``thinking:`` double that pi (and others)
        emit when the agent's reasoning pass writes the same content
        through both channels.

        SUBAGENT_PROGRESS is intentionally routed through this seam
        (not the per-event append it used to live on) so the
        watchdog audit-trail entry is still recorded; the live
        console continues to skip it via the ``emit_activity_line``
        guard so the live log and the record are still one entry
        per visible event.

        Suppresses on any writer error so a transient disk failure
        cannot break the display path.
        """
        if not body.strip() or (event_kind is ActivityEventKind.LIFECYCLE and phase is None):
            # Agent lifecycle boundaries are transport noise; only explicit pipeline
            # phase headers carry phase context into the rendered record.
            return
        writer = self._get_rendered_writer(unit_id)
        if writer is None:
            return
        # Cross-kind identical-content dedup at the seam. DA-002
        # (wt-028-display): two distinct identical ``tool_use`` events
        # must each get their own record entry, so the dedup key is
        # ``(event_kind, body)`` rather than just ``body``. The
        # companion dedup -- the ``text:`` / ``thinking:`` pair the
        # ``pi`` agent emits for the same reasoning pass -- is
        # preserved by allowing a same-body, cross-kind entry to
        # replace a previously-recorded ``TEXT`` or ``THINKING``
        # entry with the same body. That narrowed contract keeps
        # the live log and the rendered record one entry per logical
        # reasoning pass without ever silently dropping two
        # distinct same-kind tool events.
        prev = self._last_recorded_body.get(unit_id)
        if prev is not None:
            prev_kind, prev_body = prev
            if prev_kind == event_kind and prev_body == body:
                # DA-002: distinct identical same-kind events both
                # land in the record (the regression case for the
                # tool_use flood).
                pass
            elif (
                prev_body == body
                and prev_kind in (ActivityEventKind.TEXT, ActivityEventKind.THINKING)
                and event_kind in (ActivityEventKind.TEXT, ActivityEventKind.THINKING)
            ):
                # Cross-kind text/thinking companion: dedup.
                return
            elif prev_kind != event_kind and prev_body == body:
                # Other cross-kind identical content: preserve both
                # entries so the record carries the actual event
                # stream rather than silently collapsing it.
                pass
        # Build the canonical PresentedEntry so the writer's
        # formatter produces the stable field-order line.
        from ralph.display.agent_event_renderer import make_event_for_emit
        from ralph.display.presented_entry import build_presented_entry

        # wt-028-display S-5 (AC-04): when the caller did not supply
        # explicit phase / cycle / iter_, read the unit's last-known
        # run state from ``_last_phase_per_unit`` so the rendered
        # record line carries real values. Explicit caller-provided
        # values still win (e.g. ``_flush_pending_phase_headers``
        # stamps the buffered header values verbatim).
        if phase is None or cycle is None or iter_ is None:
            cached = self._last_phase_per_unit.get(unit_id)
            if cached is not None:
                cached_phase, cached_cycle, cached_iter = cached
                if phase is None:
                    phase = cached_phase
                if cycle is None:
                    cycle = cached_cycle
                if iter_ is None:
                    iter_ = cached_iter

        overflow = self._get_overflow_log(unit_id)
        visible_body, condensed = cast(
            "tuple[str, bool]",
            condense_content(
                body,
                options=CondenseOptions(
                    soft_limit=self._ctx.condenser_soft_limit,
                    hard_limit=self._ctx.condenser_hard_limit,
                    overflow_ref=overflow.relative_reference(self._workspace_root),
                ),
            ),
        )
        # The live presentation seam already preserves the unabridged body
        # in this same per-unit verbatim log. The record shares that reference;
        # writing again would duplicate the capture.
        del condensed
        event = make_event_for_emit(
            event_kind,
            visible_body,
            timestamp=timestamp,
            metadata=metadata or {},
            source=unit_id,
        )
        entry = build_presented_entry(
            event,
            unit_id=unit_id,
            timestamp=timestamp,
            phase=phase,
            cycle=cycle,
            iter_=iter_,
        )
        if phase is not None and entry.grouping_role != "phase_header":
            entry = replace(entry, indent_level=entry.indent_level + 1)
        with contextlib.suppress(Exception):
            writer.append(entry)
            self._last_recorded_body[unit_id] = (event_kind, body)

    def _emit_phase_header_record(
        self,
        phase: str,
        transition: str,
        *,
        cycle: int | None = None,
        iter_: str | None = None,
        agent_name: str | None = None,
    ) -> None:
        """Append a phase-header record entry to every active unit's record.

        S-15 (wt-028-display P1 / AC-05): phase banners on the live
        surface (``emit_phase_start`` / ``emit_phase_close_from_exit``)
        carry rich glyphs and the phase label; the corresponding
        record line is the text-first equivalent -- ``kind=lifecycle``,
        ``phase=<phase>``, ``body=phase_start`` / ``body=phase_close``,
        ``role=phase_header``, ``indent_level=0`` -- so a reader of
        ``.agent/raw/<id>.rendered.log`` can locate the phase
        boundaries without parsing Rich output. The entry is written
        to every unit that already has a writer (i.e. units that have
        produced visible events this run); units with no writer yet
        are skipped because the phase banner is a global event and
        creating an empty record for a phase-only run would create
        stale ``.agent/raw/<id>.rendered.log`` files.

        ``transition`` is the canonical body token (``phase_start``
        or ``phase_close``); a future transition (``phase_pause``,
        ``phase_resume``) extends the contract by adding one new
        token, never a new field.
        """
        timestamp = self._format_timestamp(self._clock())
        # The header fields carry the phase context; a lifecycle token has no
        # reader value once that context is present.
        body = transition
        del agent_name
        # wt-028-display S-5 (AC-04): every unit's last-known phase
        # state is cached here so subsequent ``_append_recorded_entry``
        # calls (for ordinary activity events) can populate their
        # ``phase`` / ``cycle`` / ``iter_`` fields with the live run
        # state instead of leaving them ``None``. The cache is keyed
        # per unit and bounded by ``drop_unit``.
        self._last_phase_per_unit.clear()
        for unit_id in self._rendered_writers:
            self._last_phase_per_unit[unit_id] = (phase, cycle, iter_)
        if not self._rendered_writers:
            # No writer yet: buffer the header so the first writer
            # spawn can flush it (S-15 / AC-05). The header carries
            # the same body so the rendered record line is identical
            # whether the writer existed at phase_start time or not.
            self._pending_phase_headers.append(
                {
                    "body": body,
                    "timestamp": timestamp,
                    "phase": phase,
                    "cycle": cycle,
                    "iter_": iter_,
                }
            )
            return
        for unit_id in list(self._rendered_writers.keys()):
            self._append_recorded_entry(
                unit_id,
                event_kind=ActivityEventKind.LIFECYCLE,
                body=body,
                timestamp=timestamp,
                phase=phase,
                cycle=cycle,
                iter_=iter_,
            )

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

    def _preview_header_metadata(
        self, tool_name: str, input_dict: dict[str, object], metadata: dict[str, object]
    ) -> dict[str, object] | None:
        """Remove file-preview content from the generic tool-call header."""
        if payload_from_tool_event(tool_name, {"input": input_dict}) is None:
            return None
        preview_keys = {
            "content",
            "patch",
            "oldText",
            "newText",
            "old_string",
            "new_string",
            "edits",
        }
        header_metadata = dict(metadata)
        header_metadata["input"] = {
            key: value for key, value in input_dict.items() if key not in preview_keys
        }
        return header_metadata

    def _emit_activity_event(
        self,
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        _raw_ref: str | None,
        metadata: dict[str, object] | None = None,
        timestamp: str | None = None,
    ) -> None:
        """Render an agent event through the single registry and emit it.

        After the wt-028-display consolidation, every agent-event
        formatting decision lives in
        :mod:`ralph.display.agent_event_renderer`. This function
        constructs/normalize a canonical :class:`AgentActivityEvent`
        at the ingestion boundary (via
        :func:`agent_event_renderer.make_event_for_emit`) so the
        loose ``(kind, content, metadata)`` arguments are normalized
        to the same typed event the registry consumes, then calls
        :func:`agent_event_renderer.render_event` directly. This
        function owns the *delivery* of the event (overflow tracking,
        badge wrapping, drop-warning, subscriber metadata) and
        forwards the visible text into ``emit_activity_line`` so the
        standard timestamp + level + cat badge contract is preserved.
        The same registry powers the pipeline runner's
        ``_render_agent_activity_line`` and the activity-router's
        ``render_event_line`` so the same logical event renders
        identically regardless of which path produced it
        (AC-06/AC-07/AC-08).

        ``timestamp`` (DA-003 / wt-028-display): the optional
        source-event ISO-8601 timestamp the parser pipeline
        extracted from the agent output. When supplied, it
        replaces the ``datetime.now(UTC)`` fallback at the
        boundary so the rendered record carries the source
        event's real time instead of the display clock.
        """
        metadata = {} if metadata is None else metadata
        text_content = content or ""

        # Normalize loose render args to a canonical
        # :class:`AgentActivityEvent` BEFORE rendering so the registry
        # owns every presentation decision and this ingestion site
        # cannot drift from the pipeline runner / activity-router
        # paths. ``timestamp`` (DA-003): when the caller supplies a
        # source-event ISO-8601 timestamp we forward it into the
        # canonical event so the rendered record carries the source
        # time instead of the display clock fallback.
        event = make_event_for_emit(
            kind,
            text_content,
            timestamp=timestamp,
            metadata=metadata,
            source=unit_id,
        )

        # A parser's badge-only unknown event has no operator-facing body.
        # Drop it before registry rendering so it cannot become an empty WARN row.
        if kind is ActivityEventKind.UNKNOWN:
            from ralph.display.presented_entry import build_presented_entry

            if not build_presented_entry(event, unit_id=unit_id).body:
                return

        tool_signature: tuple[str, str] | None = None

        # Hoist the TOOL_USE input_dict to method scope so the additive
        # edit-preview print (after the header line) can reach it. The
        # variable is only populated for TOOL_USE events; the preview
        # builder returns None for non-content events so the value is
        # only consumed when it matters.
        input_dict: dict[str, object] = {}

        if kind is ActivityEventKind.TOOL_USE:
            # Subscriber delivery still needs the raw tool name +
            # structured input fields so audit/recap paths keep
            # working. Rendering flows through the registry; delivery
            # decisions (record_activity) stay here.
            input_obj = metadata.get("input", metadata.get("args"))
            input_dict = cast("dict[str, object]", input_obj) if isinstance(input_obj, dict) else {}
            original_name = text_content
            tool_path = strip_terminal_control(str(input_dict.get("path", "") or ""))
            tool_workdir = strip_terminal_control(str(input_dict.get("workdir", "") or ""))
            tool_command = strip_terminal_control(str(input_dict.get("command", "") or ""))
            tool_pattern = strip_terminal_control(str(input_dict.get("pattern", "") or ""))
            tool_signature = (original_name, tool_path)
            # Subscriber receives the registry-rendered text so the
            # recorded line matches what the operator sees in the log.
            sub_line = render_event(
                event,
                unit_id=unit_id,
                active_identities=(*self._last_recorded_body, unit_id),
                escape_body=False,
            ).plain
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
            # DA-004: structured file previews own their content; retaining
            # edit/patch text in the generic header says the same thing twice.
            header_metadata = self._preview_header_metadata(original_name, input_dict, metadata)
            if header_metadata is not None:
                event = make_event_for_emit(
                    kind,
                    text_content,
                    timestamp=timestamp,
                    metadata=header_metadata,
                    source=unit_id,
                )

        # ALL formatting goes through the registry -- the friendly name,
        # formatted input, agent prefix, and non-color icon + label
        # carrier all come from ``render_event`` so this path cannot
        # drift from the pipeline runner's path. We pass the canonical
        # typed event to the registry directly; the registry's own
        # cell-aware 200-cell cap is bypassed here by reaching for
        # ``text.plain`` BEFORE truncation, so the condenser owns the
        # soft/hard overflow path (so an over-soft-limit line still
        # picks up the ``[see .agent/raw/unit-N.log]`` ref), and the
        # registry's default 200-cell cap would otherwise pre-truncate
        # short of ``soft_limit`` and silently bypass overflow tracking.
        text = strip_terminal_control(
            render_event(
                event,
                unit_id=unit_id,
                active_identities=(*self._last_recorded_body, unit_id),
                escape_body=False,
            ).plain
        )
        if kind is ActivityEventKind.TOOL_USE and isinstance(
            metadata.get("input", metadata.get("args")), dict
        ):
            # A structured parser event already exposes its tool input in the
            # call body. Remove the renderer's leading pairing chrome, while
            # preserving an arrow that was actually part of the source body.
            text = text.replace(" ↳ ", " ", 1)
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

        # A tool result's own row is its summary. Rendering the condenser's
        # headline as a supplement duplicates that event in the live log.
        effective_summary_line = None if kind is ActivityEventKind.TOOL_RESULT else summary_line

        # S-7 (wt-028-display P1): streaming kinds must buffer raw content,
        # not the registry-rendered visible text. The registry's render_event
        # prepends ``\u25d0 RUN <ts> <unit_id>`` to each fragment; if that
        # pre-formatted text were stored in ``_active_block`` and joined at
        # close, the joined passage would carry the prefix between every
        # fragment. The close path formats the joined passage itself via
        # ``_build_line`` so per-fragment formatting must be skipped here.
        content_for_emit = text_content if kind.value in _STREAMING_KINDS else visible
        result_preview_tool_name = ""
        result_preview_input: dict[str, object] = {}
        result_previewable = False
        previewed_result = kind is ActivityEventKind.TOOL_RESULT and not outcome_is_failure(
            metadata
        )
        if previewed_result:
            result_preview_tool_name, result_preview_input, result_previewable = (
                self._result_preview_input(unit_id, metadata, text_content)
            )
            previewed_result = (
                unit_id in self._last_emitted_tool_signature
                and result_previewable
                and build_edit_preview(
                    result_preview_tool_name,
                    result_preview_input,
                    width=self._ctx.width,
                    terminal_bg_is_light=self._terminal_bg_is_light,
                    overflow_ref=overflow_ref,
                    glyphs_enabled=self._ctx.glyphs_enabled,
                    diff_fills=diff_fill_styles(self._terminal_bg_is_light),
                )
                is not None
            )
            if previewed_result:
                content_for_emit = f"↳ {result_preview_tool_name}"
        # S-7 (wt-028-display P1): SUBAGENT_PROGRESS is a watchdog-side
        # companion event. The audit trail (rendered record writer below)
        # still records it, but the live console must not surface a
        # ``[progress][unit] \u25d0 RUN ... <summary>`` line alongside the
        # close-path entry that already carries the same content -- one
        # event, one visible line.
        if kind is not ActivityEventKind.SUBAGENT_PROGRESS:
            # wt-028-display S-3 (DA-001): for non-streaming kinds the
            # body the wrap should use is the registry-rendered visible
            # text (so the friendly tool name ``ralph.read_file``
            # survives instead of the raw ``mcp__ralph__read_file``
            # parser-kind identifier). For streaming kinds we keep the
            # raw fragment text so the close path can join the buffered
            # passage without the registry's per-fragment chrome.
            body_text_for_wrap = (
                content_for_emit
                if previewed_result
                else text_content
                if kind.value in _STREAMING_KINDS
                else visible
            )
            # DA-002 (S-12 / AC-07): the canonical ``PresentedEntry``
            # hierarchy data drives the live log's hanging-indent
            # continuation column. The record writer already consumes
            # ``indent_level`` / ``grouping_role``; the live log now
            # consumes the same struct so the two surfaces share one
            # vocabulary (a tool result hangs one level under its
            # call, reasoning reads as one subordinated passage). The
            # builder is the canonical source -- the registry's
            # ``_KIND_TO_GROUPING`` table is the only place this
            # mapping is defined.
            from ralph.display.presented_entry import build_presented_entry

            # DA-003 (wt-028-display): the canonical
            # ``PresentedEntry`` carries the source-event timestamp
            # when one was supplied, and the display clock only when
            # the source genuinely omitted it. ``timestamp`` is the
            # ``event.timestamp`` (already normalised by
            # ``make_event_for_emit``) so we prefer the event's own
            # value over ``self._clock().isoformat()``.
            entry_timestamp = event.timestamp if event.timestamp else self._clock().isoformat()
            _entry = build_presented_entry(
                event,
                unit_id=unit_id,
                timestamp=entry_timestamp,
            )
            self.emit_activity_line(
                unit_id,
                kind.value,
                content_for_emit,
                options=_ActivityLineOptions(
                    condensed_ref=overflow_ref if condensed_flag else None,
                    condensed_flag=condensed_flag,
                    summary_line=effective_summary_line,
                    ai_summary_line=ai_summary_line,
                    tool_signature=tool_signature,
                    activity_metadata=metadata,
                    indent_level=_entry.indent_level,
                    grouping_role=_entry.grouping_role,
                    # Keep the structured preview in the record, and restore a
                    # pairing glyph only when it was explicit in the source body.
                    # The live renderer strips its own decorative pairing chrome,
                    # but that must not erase source-provided record content.
                    record_body=(
                        _record_tool_call_body(
                            preview_record_text(
                                text_content,
                                metadata,
                                overflow_ref=overflow_ref,
                                glyphs_enabled=self._ctx.glyphs_enabled,
                            )[0]
                            or (text_content if "↳" in text_content else ""),
                            text_content,
                        )
                        or None
                    )
                    if kind is ActivityEventKind.TOOL_USE
                    else "\n".join(
                        part
                        for part in (
                            preview_record_text(
                                result_preview_tool_name,
                                result_preview_input,
                                overflow_ref=overflow_ref,
                                glyphs_enabled=self._ctx.glyphs_enabled,
                            )[0],
                            text_content,
                        )
                        if part
                    )
                    if result_previewable
                    and result_preview_tool_name == "read_file"
                    and text_content.lstrip().startswith("{")
                    else text_content
                    if kind is ActivityEventKind.TOOL_RESULT
                    else None,
                ),
                # DA-003 (wt-028-display): forward the
                # source-event timestamp so the rendered record
                # carries the parser time, not the display clock.
                source_timestamp=event.timestamp or None,
                # DA-002 (S-4): pass the wrap-target body so the wrap
                # uses the standalone body instead of the full
                # chrome-prefixed rendered text. The chrome prefix
                # (``icon label ts ↳ read_file u1``) is rendered on
                # the first line by ``emit_activity_line``; the
                # continuation wraps the body alone at the badge
                # column so the 40-col floor produces readable
                # multiword chunks rather than one-character
                # fragments.
                body_text=body_text_for_wrap,
            )

            if kind is ActivityEventKind.TOOL_USE or previewed_result:
                self._emit_activity_preview(
                    unit_id,
                    kind,
                    result_preview_tool_name if previewed_result else text_content,
                    result_preview_input if previewed_result else metadata,
                    entry_timestamp,
                    include_header=True,
                )

        # S-13 (wt-028-display P1 / AC-02 / AC-03): the rendered
        # record append now lives at the shared presentation seam
        # (the ``emit_activity_line`` print path and the
        # ``_close_block`` single close entry). The per-event append
        # here is gone so the file surface cannot drift ahead of the
        # terminal surface (the original bug: raw events appended
        # upstream of the streaming-block coalescing produced
        # doubled / fragmented rows the operator never saw).
        #
        # DA-001 (S-2): SUBAGENT_PROGRESS is a watchdog companion
        # event. The watchdog's per-channel evidence surface stays
        # fresh via the ``invoke_subagent_sink`` contextvar path
        # reached upstream of this function (see
        # ``ralph.pipeline.activity_stream``); recording an
        # additional ``role=progress`` audit-trail entry on top of
        # the visible event's close entry was the duplication the
        # corpus showed. The live console emission is suppressed
        # above (the SUBAGENT_PROGRESS guard around the
        # ``emit_activity_line`` call), so the file surface and the
        # terminal surface both present one entry per visible
        # event.
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
        self._flush_pending_tool_results()
        # P0 (wt-028-display S-11 / AC-07): flush any per-unit
        # rendered-record writer that was not already collected by
        # ``drop_unit`` (e.g. a single-wave run whose drop_unit is
        # never called). Each writer is disabled after flush so a
        # second ``stop()`` is a no-op.
        for writer in self._rendered_writers.values():
            with contextlib.suppress(Exception):
                writer.flush()
            with contextlib.suppress(Exception):
                writer.disable()
        self._rendered_writers.clear()
        # S-23 (wt-028-display P1 / AC-06): flush the verbatim
        # overflow logs too so the condensed-content marker on the
        # rendered record line points at a file that has the
        # unabridged body on disk. ``RawOverflowLog`` buffers in
        # userspace; without this flush the marker would advertise a
        # reference that is empty until the 5-second flush interval
        # elapses (or run end never arrives).
        for overflow in self._overflow_logs.values():
            with contextlib.suppress(Exception):
                overflow.flush()

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
        # Visible transcript content is normalized via strip_markup so the
        # subscriber snapshot and rendered transcript both carry plain text
        # (Rich markup reduced). The transcript sink path (emit_log_line /
        # kind="raw") still preserves literal brackets for raw log payloads.
        sanitized_line = strip_markup(line)
        if unit_id is not None:
            with contextlib.suppress(Exception):
                self._subscriber.record_activity(
                    unit_id=unit_id,
                    line=sanitized_line,
                    agent_name=unit_id,
                )
        self.emit_log_line(unit_id or "run", sanitized_line)

    def emit_parsed_event(
        self,
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        metadata: dict[str, object],
        timestamp: str | None = None,
    ) -> None:
        """Buffer consecutive terminal results so transport floods collapse."""
        if kind is ActivityEventKind.TOOL_RESULT and content is not None:
            clean_content = _clean_tool_result_content(content, unit_id)
            # Only transport-shaped results wait for a possible burst. Ordinary
            # results remain synchronous so a completed tool call is visible at
            # once instead of being held until a later event or run shutdown.
            if clean_content == " ".join(content.split()):
                self._flush_pending_tool_result(unit_id)
                self._emit_parsed_event_now(unit_id, kind, clean_content, metadata, timestamp)
                return
            arrived_at = self._monotonic()
            pending = self._pending_tool_results.get(unit_id)
            signature = (clean_content, str(metadata.get("tool", metadata.get("tool_name", ""))))
            if (
                pending is not None
                and signature
                == (pending[0], str(pending[1].get("tool", pending[1].get("tool_name", ""))))
                and self._within_tool_result_burst(pending[3], timestamp, pending[4], arrived_at)
            ):
                self._pending_tool_results[unit_id] = (
                    pending[0],
                    pending[1],
                    pending[2],
                    timestamp,
                    arrived_at,
                    pending[5] + 1,
                )
                return
            self._flush_pending_tool_result(unit_id)
            self._pending_tool_results[unit_id] = (
                clean_content,
                dict(metadata),
                timestamp,
                timestamp,
                arrived_at,
                1,
            )
            return
        self._flush_pending_tool_result(unit_id)
        self._emit_parsed_event_now(unit_id, kind, content, metadata, timestamp)

    @staticmethod
    def _within_tool_result_burst(
        previous: str | None,
        current: str | None,
        previous_arrival: float,
        current_arrival: float,
    ) -> bool:
        """Return whether adjacent results arrived within one second."""
        if previous is not None and current is not None:
            with contextlib.suppress(ValueError):
                return (
                    abs(
                        (
                            datetime.fromisoformat(current.replace("Z", "+00:00"))
                            - datetime.fromisoformat(previous.replace("Z", "+00:00"))
                        ).total_seconds()
                    )
                    <= 1.0
                )
        return current_arrival - previous_arrival <= 1.0

    def _flush_pending_tool_results(self) -> None:
        for unit_id in tuple(self._pending_tool_results):
            self._flush_pending_tool_result(unit_id)

    def _flush_pending_tool_result(self, unit_id: str) -> None:
        pending = self._pending_tool_results.pop(unit_id, None)
        if pending is None:
            return
        content, metadata, timestamp, _last_timestamp, _last_arrival, count = pending
        self._emit_parsed_event_now(
            unit_id, ActivityEventKind.TOOL_RESULT, content, metadata, timestamp
        )
        if count >= _MIN_TOOL_RESULT_COLLAPSE_COUNT:
            hidden_count = count - 1
            hidden_bytes = hidden_count * len(content.encode())
            overflow = self._get_overflow_log(unit_id)
            overflow.append("\n".join([content] * hidden_count))
            self._check_overflow_size(unit_id, overflow)
            marker = (
                f"… {hidden_count} more identical results ({hidden_bytes} B) "
                f"[see {overflow.relative_reference(self._workspace_root)}]"
            )
            self._emit_parsed_event_now(
                unit_id, ActivityEventKind.TOOL_RESULT, marker, metadata, timestamp
            )

    def _emit_parsed_event_now(
        self,
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        metadata: dict[str, object],
        timestamp: str | None = None,
    ) -> None:
        """Route a pre-parsed agent event through the structured activity path.

        ``timestamp`` (DA-003 / wt-028-display): the optional
        source-event ISO-8601 timestamp the parser pipeline
        extracted from the agent output. When supplied, it is
        forwarded all the way to ``_emit_activity_event`` and
        from there into the rendered record line.
        """
        if (
            kind in (ActivityEventKind.LIFECYCLE, ActivityEventKind.UNKNOWN)
            and content is not None
            and _is_bare_lifecycle(content)
        ):
            return
        # S-7 (wt-028-display P1): SUBAGENT_PROGRESS still reaches
        # ``_emit_activity_event`` here so the event-emission contract
        # (``stream_parsed_agent_activity`` -> ``display.emit_parsed_event``
        # carries the watchdog companion event end-to-end) is preserved
        # for tests and other subscribers that hook ``_emit_activity_event``.
        # The LIVE console emission is suppressed inside
        # ``_emit_activity_event`` itself (the watchdog's audit trail still
        # records the event via the rendered record writer path).
        #
        # DA-003 (wt-028-display S-2): when the caller did not supply a
        # source-event timestamp, default to the display clock (NOT
        # ``datetime.now(UTC)``) so the rendered record carries the
        # injected test clock and the production-time clock both stay
        # deterministic. ``make_event_for_emit`` still falls back to
        # ``datetime.now(UTC)`` for callers that bypass this seam (e.g.
        # direct registry calls), but the production path goes through
        # here.
        if timestamp is None:
            timestamp = self._clock().isoformat()
        call_id = tool_call_id(metadata)
        if kind is ActivityEventKind.TOOL_USE and call_id:
            recent = self._recorded_tool_call_ids.get(unit_id, ())
            if call_id in recent:
                return
            self._recorded_tool_call_ids[unit_id] = (*recent, call_id)[-64:]
        elif (
            kind in (ActivityEventKind.TOOL_RESULT, ActivityEventKind.ERROR) and content is not None
        ):
            self._last_tool_result_content[unit_id] = (call_id, content)
        elif kind is ActivityEventKind.TEXT and content is not None:
            previous = self._last_tool_result_content.pop(unit_id, None)
            if previous is not None:
                previous_id, previous_content = previous
                same_call = call_id is not None and call_id == previous_id
                idless_companion = (
                    call_id is None
                    and bool(previous_content)
                    and (
                        content == previous_content
                        or content in previous_content
                        or previous_content in content
                    )
                )
                if (same_call and content == previous_content) or idless_companion:
                    return
        else:
            self._last_tool_result_content.pop(unit_id, None)
        record_metadata = dict(metadata)
        emitted_content = content
        if kind is ActivityEventKind.TOOL_USE and not call_id:
            input_obj = record_metadata.get("input", record_metadata.get("args"))
            input_dict = cast("dict[str, object]", input_obj) if isinstance(input_obj, dict) else {}
            if not any(input_dict.get(key) for key in ("path", "command", "pattern")):
                # ponytail: a global call ordinal is enough to make unknown-target calls skimmable.
                target = f"call {self._run_counters.tool_calls + 1}"
                record_metadata["target"] = target
                emitted_content = f"{content or record_metadata.get('tool_name', 'call')} {target}"
        self._emit_activity_event(unit_id, kind, emitted_content, None, record_metadata, timestamp)

    def _on_activity_router_event(
        self,
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_reference: str | None,
        metadata: dict[str, object] | None,
    ) -> None:
        """Adapter between ``ActivityRouter.on_event`` and ``_emit_activity_event``.

        S-7 (wt-028-display P1): the router forwards SUBAGENT_PROGRESS
        events for every thinking / text / tool line so the watchdog
        sink stays fresh. The watchdog sink is reached BEFORE this
        adapter runs (via ``invoke_subagent_sink`` in
        ``ActivityRouter._dispatch_subagent_progress``), so dropping the
        event here does not lose watchdog coverage. Dropping it here
        also prevents the per-fragment watchdog summary from closing the
        active streaming block before the operator sees the joined
        passage, and from emitting a duplicate progress line alongside
        the close entry.
        """
        if kind is ActivityEventKind.SUBAGENT_PROGRESS:
            return
        self._emit_activity_event(unit_id, kind, content, raw_reference, metadata)

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
        """Emit a one-time run-start orientation block at pipeline start.

        DA-005 (S-6 / AC-05): on a height-constrained console
        (``height <= 12``) the visual chrome (section rule blank
        line + glyph banner) compresses before any information is
        dropped; the orientation rows stay visible, condensed into a
        single unboxed headed block, so the same information reaches
        the operator on a 12-row split pane or a magnified screen.
        """
        if self._is_quiet:
            return
        height_constrained = self._ctx.is_height_constrained()
        with contextlib.suppress(Exception):
            if not height_constrained:
                self._emit_section_rule("[run-start]")
            timestamp = self._format_timestamp(self._clock())

            t = _RichText()
            # wt-028-display S-4: the run-start header drops the
            # MILESTONE LEVEL and META category chrome — severity
            # is carried by the milestone glyph in the body, exactly
            # once per the AC-03 single-severity contract.
            t.append(f"{timestamp} ")
            t.append(
                f"[run-start] {self._ctx.glyph_for('milestone')} ",
                style="theme.banner.ascii",
            )
            t.append("Ralph Workflow run start", style="theme.banner.title")
            self._console.print(t, markup=False, highlight=False, no_wrap=True)

            self._emit_run_start(timestamp, orientation, height_constrained=height_constrained)

    #: DA-003 (wt-028-display S-6 / AC-05): column prefix the
    #: ``_build_line`` chrome (timestamp + level + category + tag)
    #: takes on a run-start line. The body has to fit within
    #: ``body_measure() - chrome_prefix_len`` columns so a
    #: long ``workspace_root`` left-elides to that budget rather
    #: than silently overflowing the terminal width.
    _RUN_START_CHROME_PREFIX_LEN: int = len("00:00:00 INFO META [run-start] ")

    def _emit_run_start(
        self,
        timestamp: str,
        orientation: RunStartOrientation,
        *,
        height_constrained: bool = False,
    ) -> None:
        """Emit the run-start orientation body (single default-mode layout).

        DA-003 (S-6 / AC-05): the height-constrained console path
        renders one heading line plus one ``key=value`` line per
        supplied field. The body of each line is left-elided to fit
        within ``body_measure() - chrome_prefix_len`` so a long
        ``workspace_root`` or ``prompt_path`` cannot silently clip
        later fields the way the previous single ``no_wrap=True``
        line did -- a 12-row, 80-col probe at fully-populated
        orientation rendered only ``prompt=...`` and dropped
        ``developer``, ``iterations``, ``parallel``, ``plan``, and
        ``verbosity``. With the structured layout every supplied
        field has its own line and either renders in full, left-elides
        with an accounted-for marker, or condenses to a counted
        marker that points at the verbatim capture.
        """
        pw_parts: list[tuple[str, str]] = []
        if orientation.prompt_path is not None:
            pw_parts.append(("prompt", strip_markup(orientation.prompt_path)))
        if orientation.workspace_root is not None:
            pw_parts.append(("workspace", strip_markup(orientation.workspace_root)))
        agents_parts: list[tuple[str, str]] = []
        if orientation.developer_agent is not None:
            agents_parts.append(("developer", strip_markup(orientation.developer_agent)))
        if orientation.developer_model is not None:
            agents_parts.append(("model", strip_markup(orientation.developer_model)))
        iter_parts: list[tuple[str, str]] = []
        if orientation.developer_iters is not None:
            iter_parts.append(("iterations", f"dev:{orientation.developer_iters}"))
        parallel_parts: list[tuple[str, str]] = []
        if orientation.parallel_max_workers is not None:
            parallel_parts.append(("parallel", f"max_workers={orientation.parallel_max_workers}"))
        plan_val = "ready" if orientation.plan_present else "absent"
        plan_parts: list[tuple[str, str]] = [("plan", plan_val)]
        if orientation.verbosity is not None:
            plan_parts.append(("verbosity", orientation.verbosity))

        if height_constrained:
            self._emit_run_start_unboxed(
                timestamp,
                pw_parts=pw_parts,
                agents_parts=agents_parts,
                iter_parts=iter_parts,
                parallel_parts=parallel_parts,
                plan_parts=plan_parts,
            )
            return

        for key, value in pw_parts:
            self._console.print(
                self._build_line(timestamp, "INFO", "META", f"[run-start] {key}={value}"),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        for key, value in agents_parts:
            self._console.print(
                self._build_line(timestamp, "INFO", "META", f"[run-start] {key}={value}"),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        for key, value in iter_parts:
            self._console.print(
                self._build_line(timestamp, "INFO", "META", f"[run-start] {key}={value}"),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        for key, value in parallel_parts:
            self._console.print(
                self._build_line(timestamp, "INFO", "META", f"[run-start] {key}={value}"),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

        for key, value in plan_parts:
            self._console.print(
                self._build_line(timestamp, "INFO", "META", f"[run-start] {key}={value}"),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

    def _emit_run_start_unboxed(
        self,
        timestamp: str,
        *,
        pw_parts: list[tuple[str, str]],
        agents_parts: list[tuple[str, str]],
        iter_parts: list[tuple[str, str]],
        parallel_parts: list[tuple[str, str]],
        plan_parts: list[tuple[str, str]],
    ) -> None:
        """Emit the run-start body on a height-constrained console.

        DA-003 (wt-028-display S-6 / AC-05): one heading line plus
        one ``key=value`` line per supplied field. Each body is
        left-elided to fit the available body budget so a long
        ``workspace_root`` cannot silently clip later fields -- the
        pre-fix single ``no_wrap=True`` line on a 12-row, 80-col
        probe at fully-populated orientation dropped
        ``iterations``, ``parallel``, ``plan``, and ``verbosity``
        after the path consumed the entire row. Each line
        carries the indentation of the chrome prefix so the
        sub-rows hang under the heading row. Body budgets below
        the 40-column floor fall back to a counted condensation
        marker so the field is never silently dropped.
        """
        # Heading line: still tags the run-start so the entry is
        # findable in scrollback / the record.
        self._console.print(
            self._build_line(timestamp, "INFO", "META", "[run-start] orientation"),
            markup=False,
            highlight=False,
            no_wrap=True,
        )
        chrome_prefix_len = self._RUN_START_CHROME_PREFIX_LEN + len("  ")
        # The body budget must accommodate the 2-space hanging indent
        # that aligns each ``key=value`` row beneath the heading. We
        # floor at 12 so the smallest sane token still survives --
        # below that we condense to a counted marker rather than
        # silently dropping the field.
        body_budget = max(12, self._ctx.body_measure() - chrome_prefix_len)

        groups: list[tuple[str, list[tuple[str, str]]]] = [
            ("prompt", pw_parts),
            ("agents", agents_parts),
            ("iterations", iter_parts),
            ("parallel", parallel_parts),
            ("plan", plan_parts),
        ]
        condensed_count = 0
        condensed_chars = 0
        for _group_label, items in groups:
            for key, value in items:
                rendered = self._render_run_start_field(timestamp, key, value, body_budget)
                if rendered is None:
                    condensed_count += 1
                    condensed_chars += len(value)
                else:
                    self._console.print(rendered, markup=False, highlight=False)
        if condensed_count:
            marker = (
                f"\u22ee {condensed_count} field"
                f"{'s' if condensed_count != 1 else ''} condensed"
                f" \u00b7 {condensed_chars} chars"
                " \u00b7 in verbatim capture"
            )
            self._console.print(
                self._build_line(timestamp, "INFO", "META", f"[run-start] {marker}"),
                markup=False,
                highlight=False,
                no_wrap=True,
            )

    def _render_run_start_field(
        self,
        timestamp: str,
        key: str,
        value: str,
        body_budget: int,
    ) -> Text | None:
        """Render a single ``key=value`` row, left-eliding long values.

        DA-003 (wt-028-display S-6 / AC-05): when the rendered
        ``key=value`` line fits within ``body_budget`` columns the
        value is rendered in full; when it does not the value is
        left-elided (``\u2026`` prefix) until it does. Returns
        ``None`` when the budget cannot accommodate even a
        counted marker for the field so the caller can collapse to
        a single ``N fields condensed \u00b7 M chars \u00b7 in verbatim
        capture`` marker at the end of the block.
        """
        rendered = f"{key}={value}"
        if cell_len(rendered) <= body_budget:
            line = self._build_line(timestamp, "INFO", "META", f"[run-start]   {rendered}")
            return line
        # Leave room for ``key=`` plus the elision glyph and the
        # closing ``\u2026`` so the operator still sees which key this
        # row carried. If the budget cannot even hold ``key=\u2026`` the
        # caller counts this row in the block-level marker.
        keep_suffix = "\u2026"
        key_prefix = f"{key}="
        available = body_budget - cell_len(key_prefix) - cell_len(keep_suffix)
        if available <= 0:
            return None
        suffix_chars: list[str] = []
        suffix_width = 0
        for char in reversed(value):
            char_width = cell_len(char)
            if suffix_width + char_width > available:
                break
            suffix_chars.append(char)
            suffix_width += char_width
        elided = f"{keep_suffix}{''.join(reversed(suffix_chars))}"
        rendered = f"{key_prefix}{elided}"
        if cell_len(rendered) > body_budget:
            raise RuntimeError("run-start field exceeded its cell-width budget")
        return self._build_line(timestamp, "INFO", "META", f"[run-start]   {rendered}")

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
        clean_produced = strip_markup(produced).strip()
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
        # S-15 (AC-05): write the phase-close record entry outside
        # the suppressed body block so a record-side error cannot
        # roll back the live emission (or vice versa). The
        # ``_append_recorded_entry`` helper is itself suppress-
        # guarded, so a phase-close record failure stays invisible.
        with contextlib.suppress(Exception):
            self._emit_phase_header_record(
                exit_model.phase_name,
                "phase_close",
                cycle=exit_model.outer_dev_iteration,
                iter_=(
                    f"{exit_model.inner_analysis}/{exit_model.inner_analysis_cap}"
                    if exit_model.inner_analysis is not None
                    and exit_model.inner_analysis_cap is not None
                    else None
                ),
            )
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
            # wt-028-display S-4: the run-end header drops the
            # MILESTONE LEVEL and META category chrome — severity is
            # carried by the milestone glyph in the body, exactly
            # once per the AC-03 single-severity contract.
            t.append(f"{timestamp} ")
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
                        timestamp, "INFO", "META", f"[run-end] pr={strip_markup(pr_url)}"
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
                _exit_trigger_label,
                render_completion_summary,
                style_for_role,
                style_for_terminal_failure,
            )
            from ralph.display.phase_status import format_elapsed_seconds

            resolved_options = options if options is not None else CompletionSummaryOptions()
            # DA-003 (wt-028-display S-6 / AC-05): on a height-constrained
            # console (``height <= 12``) the full Rich Group of
            # rules/sections collapses to an unboxed condensed heading.
            # The bordered layout (Plan / Metrics / Decisions / Review /
            # Analysis / Iteration Context / Activity / Commit /
            # auto-integrate / tail / closing rule) would consume the
            # entire 12-row working area; the condensed heading keeps
            # the outcome + essential counts in 4 rows or fewer so the
            # status bar / scrollback stay usable. The information is
            # the same; the visual chrome is dropped.
            if self._ctx.is_height_constrained():
                failed = snapshot.is_terminal_failure
                style = (
                    style_for_terminal_failure(resolved_options.pipeline_policy)
                    if failed
                    else style_for_role("terminal", resolved_options.pipeline_policy)
                )
                title = "Pipeline Failed" if failed else "Pipeline Complete"
                heading = Text()
                heading.append(title, style=style)
                self._console.print(heading)
                self._console.print(
                    Text(
                        f"  exit={_exit_trigger_label(snapshot)}"
                        + (
                            f"  elapsed={format_elapsed_seconds(resolved_options.elapsed_seconds)}"
                            if resolved_options.elapsed_seconds is not None
                            else ""
                        )
                        + f"  agent_calls={snapshot.total_agent_calls}",
                        style="theme.text.muted",
                    )
                )
                if snapshot.is_terminal_failure and snapshot.last_error:
                    self._console.print(
                        Text(f"  error: {snapshot.last_error}", style="theme.level.error")
                    )
                # DA-005 (S-6 / AC-05): on a height-constrained console
                # the bordered layout drops Plan / Metrics / Decisions /
                # Review / Analysis / Iteration / Activity / Commit /
                # auto-integrate sections; emit a single accounted-for
                # marker naming what was condensed so the reader knows
                # the omission is intentional, not a bug. The marker
                # uses the same vocabulary as the live log's content
                # condensation markers (``-- condensed`` / ``-- in <file>``)
                # so it reads as one rule, not a new convention.
                condensed_sections = (
                    "Plan",
                    "Metrics",
                    "Decisions",
                    "Review",
                    "Analysis",
                    "Iteration",
                    "Activity",
                    "Commit",
                    "auto-integrate",
                )
                record_path = rendered_record_path(
                    resolved_options.workspace_root or Path(), snapshot.active_agent or "unknown"
                )
                condensed_chars = len(
                    render_completion_summary(snapshot, options=resolved_options).plain
                )
                self._console.print(
                    Text(
                        f"  -- {len(condensed_sections)} sections condensed · "
                        f"{condensed_chars} chars · in {record_path}",
                        style="theme.text.muted",
                    )
                )
            else:
                from ralph.display.completion_summary import (
                    render_completion_summary_group,
                )

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

        Port of the retired ralph.display.phase_banner.show_phase_start helper.
        """
        if self._is_quiet:
            return
        self.flush_blocks()
        with contextlib.suppress(Exception):
            self._emit_section_rule("")
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
        # S-15 (AC-05): write the phase-header record entry so the
        # text-first surface carries the phase boundary.
        with contextlib.suppress(Exception):
            self._emit_phase_header_record(
                phase,
                "phase_start",
                agent_name=agent_name,
            )

    def emit_phase_start_from_entry(
        self,
        entry: PhaseEntryModel,
        *,
        pipeline_policy: PipelinePolicy | None = None,
    ) -> None:
        """Display the start of a pipeline phase from a lifecycle entry model.

        Port of the retired ralph.display.phase_banner.show_phase_start_from_entry helper.
        Canonical model-based path (single default-mode layout): emits a
        titled Rule with phase label, outer development iteration,
        inner analysis iteration, and an optional agent line.
        """
        if self._is_quiet:
            return
        self.flush_blocks()
        with contextlib.suppress(Exception):
            self._emit_section_rule("")
            c = self._console
            style = _phase_style(entry.phase_name, pipeline_policy)
            label = entry.human_label()
            start_glyph = self._ctx.glyph_for("start")
            od_glyph = self._ctx.glyph_for("outer_dev")
            ia_glyph = self._ctx.glyph_for("inner_analysis")
        # S-15 (AC-05): write the phase-header record entry.
        with contextlib.suppress(Exception):
            self._emit_phase_header_record(
                entry.phase_name,
                "phase_start",
                cycle=entry.outer_dev_iteration,
                iter_=(
                    f"{entry.inner_analysis}/{entry.inner_analysis_cap}"
                    if entry.inner_analysis is not None and entry.inner_analysis_cap is not None
                    else None
                ),
                agent_name=entry.agent_name,
            )

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

        Port of the retired ralph.display.phase_banner.show_phase_transition helper.
        Major transitions get a prominent Rule banner; minor transitions get
        a simple titled Rule. The leading section rule is always emitted in
        the single default mode (no per-mode gating remains).
        """
        if self._is_quiet:
            return
        self.flush_blocks()
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

        Port of the retired ralph.display.phase_banner.show_phase_close_banner helper.
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
        """Render the agent-facing plan handoff or canonical Markdown summary.

        Port of the retired artifact_renderer.render_plan_artifact helper.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[plan]")
            markdown = self._resolve_authoritative_markdown_handoff(
                workspace_root,
                "plan",
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

        Port of the retired artifact_renderer.render_development_artifact helper.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[development-result]")
            markdown = self._resolve_authoritative_markdown_handoff(
                workspace_root,
                "development_result",
            )
            if markdown:
                self._render_text_block("DEVELOPMENT RESULT", markdown, "execution")

    def emit_review_artifact(self, workspace_root: Path) -> None:
        """Render review findings using the authoritative Markdown handoff.

        Port of the retired artifact_renderer.render_review_artifact helper.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[review]")
            markdown = self._resolve_authoritative_markdown_handoff(
                workspace_root,
                "issues",
            )
            if markdown:
                self._render_text_block("REVIEW ISSUES", markdown, "review")

    def emit_fix_artifact(self, workspace_root: Path) -> None:
        """Render fix result artifacts as a titled block.

        Port of the retired artifact_renderer.render_fix_artifact helper.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[fix]")
            markdown = self._resolve_authoritative_markdown_handoff(
                workspace_root,
                "fix_result",
            )
            if markdown:
                self._render_text_block("FIX", markdown, "fix")

    def emit_analysis_decision(self, workspace_root: Path, drain: str) -> None:
        """Render an analysis decision artifact as a titled block.

        Port of the retired artifact_renderer.render_analysis_decision helper.
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

        Port of the retired artifact_renderer.render_commit_message helper.
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

        Port of the retired artifact_renderer.render_missing_plan_hint helper.
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
    def _resolve_authoritative_markdown_handoff(
        workspace_root: Path,
        artifact_type: str,
    ) -> str | None:
        """Return the submitted markdown for an artifact type.

        Markdown artifacts are the source of truth; submission writes the
        handoff copy as identical bytes, so this reads the handoff first and
        falls back to the artifact document itself. No derivation happens here.
        """
        handoff = ParallelDisplay._read_markdown_handoff(workspace_root, artifact_type)
        if handoff is not None:
            return handoff
        artifact_path = workspace_root / _ARTIFACTS_DIR / f"{artifact_type}.md"
        markdown = ParallelDisplay._read_text_defensive(artifact_path)
        if markdown is None:
            return None
        stripped = markdown.strip()
        return stripped or None

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
            self._console.print(strip_terminal_control(line), markup=False, highlight=False)
        self._console.print(Rule(style=_phase_style(style_phase)), markup=False, highlight=False)

    def _render_text_block(
        self,
        title: str,
        body: str,
        style_phase: str,
        *,
        indent: bool = False,
    ) -> None:
        del indent
        self._console.print()
        self._console.print(Rule(title, style=_phase_style(style_phase)))
        self._console.print(
            render_markdown_preview(
                body,
                width=self._ctx.width,
                terminal_bg_is_light=self._terminal_bg_is_light,
            )
        )
        self._console.print(Rule(style=_phase_style(style_phase)))

    # -- Welcome-banner, first-run-panel, table, capability-summary, status -

    def emit_first_run_panel(self, content: list[RenderableType]) -> None:
        """Print the first-run welcome Panel to ``self._ctx.console``.

        Port of the retired ralph.display.first_run_panel.render_first_run_panel helper.

        P0 (wt-028-display S-6 / AC-05 / DA-005): on a height-constrained
        console (``self._ctx.height <= 12`` per
        :meth:`DisplayContext.is_height_constrained`) the bordered
        Panel degrades to unboxed headed text so the welcome takes
        fewer rows in the working area. The information is the same;
        the visual chrome is dropped because the panel border +
        padding would crowd a 12-row split pane. The threshold is
        ``<=`` (not strict ``<``) so the canonical 12-row floor
        activates the constrained presentation -- the 12-row split
        pane is the documented accessibility path and must trigger
        the degradation on the boundary itself.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            if self._ctx.is_height_constrained():
                # Unboxed heading: title row + content lines, no
                # border, no padding. The title still carries the
                # semantic role (theme.banner.title) so a CVD /
                # no-color reader still sees the heading.
                self._console.rule(
                    "Ralph Workflow first-run setup",
                    style="theme.banner.border",
                    align="left",
                )
                for item in content:
                    self._console.print(item)
            else:
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

        Port of the retired ralph.banner.show_banner helper.

        P0 (wt-028-display S-6 / AC-05): on a height-constrained
        console the bordered ``Panel.fit`` around the ASCII-art
        banner degrades to a plain heading line so the banner
        takes one row instead of seven. The ASCII art itself is
        skipped on the constrained surface (it would dominate
        the 12-row working area); the title + version line
        carries the same identity information.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[welcome]")
            if self._ctx.is_height_constrained():
                # Unboxed heading: title + version on one line,
                # welcome + tagline on the next. The box is gone
                # but the text reads the same.
                self._console.print(
                    f"Ralph Workflow v{version}",
                    style="theme.banner.title",
                )
                self._console.print(_WELCOME_MESSAGE_TEXT, style="theme.banner.welcome")
                self._console.print(_TAGLINE_TEXT, style="theme.banner.tagline")
            else:
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

        Port of the retired ralph.cli.options.display_agents_table helper.
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
                table.add_row(Text("No agents configured", style="theme.text.muted"), "", "", "")
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

        Port of the retired ralph.cli.options.display_providers_table helper.
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

        Port of the retired ralph.display.tables.show_config helper.

        DA-004 (wt-028-display S-6 / AC-05): on a height-constrained
        console (``height <= 12``) the bordered ``Panel`` around the
        full config JSON degrades to an unboxed headed summary that
        lists the top-level config keys (without the giant nested
        JSON body). The bordered form would consume the entire
        12-row working area; the heading-only form keeps the section
        rule + a condensed key list so the operator still sees the
        effective configuration structure without scrolling past a
        90+ row bordered panel.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[config]")
            if self._ctx.is_height_constrained():
                # Unboxed heading + condensed key list: title rule,
                # then the top-level keys of the dumped config.
                # ``model_dump`` returns a dict; ``dict.keys()`` are
                # the top-level field names. The values are dropped
                # on the constrained surface because a 12-row
                # working area cannot fit the nested JSON body and
                # the field names already document the structure.
                self._console.rule(
                    "Effective Configuration",
                    style="theme.phase.planning",
                    align="left",
                )
                top_level_keys = list(config.model_dump().keys())
                if top_level_keys:
                    self._console.print(
                        Text(
                            "  " + ", ".join(top_level_keys),
                            style="theme.text.muted",
                        )
                    )
                else:
                    self._console.print(Text("(no fields)", style="theme.text.muted"))
            else:
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
            from ralph.cli._capability_summary import (
                DOCS_MCP_NOT_INSTALLED_MESSAGE,
                collect_skill_root_rows,
            )
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
                elif (
                    label.startswith("Docs MCP") and entry.status == CapabilityStatus.NOT_INSTALLED
                ):
                    status_text = Text(
                        DOCS_MCP_NOT_INSTALLED_MESSAGE,
                        style="theme.status.warning",
                    )
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

        P0 (wt-028-display S-6 / AC-05): on a height-constrained
        console the bordered Panel degrades to a heading + body
        pair (no border, no padding) so the info block fits inside
        a 12-row working area without crowding the scrollback. The
        title still carries the theme.phase.planning color so the
        reader can still locate the section.
        """
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[info]")
            if self._ctx.is_height_constrained():
                self._console.print(
                    Text(title, style="theme.phase.planning"),
                )
                # DA-001 (S-5 / AC-04): the unboxed headed body
                # also honors the body measure so a 250-col
                # console does not print 250-char prose lines from
                # an info panel. The measure is the shared cap
                # exposed by DisplayContext.body_measure(); the
                # ``soft_wrap=True`` flag preserves long single
                # lines (paths, re-run commands) without
                # truncating the operator's fix-it phrase.
                self._console.print(
                    content,
                    markup=False,
                    highlight=False,
                    soft_wrap=True,
                    no_wrap=False,
                    overflow="fold",
                    width=self._ctx.body_measure(),
                )
            else:
                # DA-001 (S-5 / AC-04): the bordered Panel must
                # constrain its content width to the shared
                # ``body_measure()`` so prose on a 250-col console
                # stops running full width. Rules, tables, and
                # aligned columns keep using ``self._ctx.width``
                # because they have their own width negotiation;
                # only prose-shaped panels honor the measure cap.
                # ``expand=False`` keeps the panel's outer width
                # fixed at the cap (Rich's default ``expand=True``
                # would still let the wrapped body reach the
                # terminal width through the panel's title bar).
                panel = Panel(
                    content,
                    title=title,
                    border_style="theme.phase.planning",
                    padding=(1, 2),
                    expand=False,
                    width=self._ctx.body_measure(),
                )
                self._console.print(panel)

    def emit_metrics_table(self, metrics: dict[str, int]) -> None:
        """Render the metrics table for pipeline summary stats.

        Port of the retired ralph.display.tables.show_metrics helper.
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

        Port of the retired ralph.display.tables.show_checkpoint_summary helper.
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
        self._overflow_warned.discard(unit_id)
        self._drop_last_warned.pop(unit_id, None)
        self._last_emitted_tool_signature.pop(unit_id, None)
        self._last_worker_states.pop(unit_id, None)
        # S-13 (wt-028-display P1 / AC-02): close any open
        # streaming block for this unit so the seam-append path
        # emits the close entry (carrying the joined passage, span,
        # and duration) and the rendered record receives the
        # matching single entry. Without this, ``drop_unit`` would
        # flush an empty writer for streaming-kinds runs and the
        # close entry would vanish on the file surface.
        # S-23 (wt-028-display P1): the close MUST run BEFORE the
        # overflow log is popped so the close path's appended
        # buffered full payload lands in the same RawOverflowLog
        # the original event opened (and that drop_unit will close).
        if unit_id in self._active_block:
            with contextlib.suppress(Exception):
                self._close_block(unit_id, self._format_timestamp(self._clock()))
        self._active_block.pop(unit_id, None)
        self._active_block_chars.pop(unit_id, None)
        self._last_checkpoint_chars.pop(unit_id, None)
        self._last_recorded_body.pop(unit_id, None)
        self._recorded_tool_call_ids.pop(unit_id, None)
        self._last_tool_result_content.pop(unit_id, None)
        self._flush_pending_tool_result(unit_id)
        self._last_text_thinking_block_close.pop(unit_id, None)
        # wt-028-display S-5 (AC-04): clear the per-unit last-phase
        # cache so a re-spawned worker does not carry a stale
        # phase / cycle / iter_ across drop_unit boundaries.
        self._last_phase_per_unit.pop(unit_id, None)
        # S-23 (wt-028-display P1): close the overflow log AFTER
        # the streaming-block close so the buffered full payload
        # lands in the same handle drop_unit is about to close.
        overflow = self._overflow_logs.pop(unit_id, None)
        if overflow is not None:
            with contextlib.suppress(Exception):
                overflow.flush()
            with contextlib.suppress(Exception):
                overflow.close()
        # P0 (wt-028-display S-11 / AC-07): the rendered-record
        # writer is per-unit; ``drop_unit`` flushes the buffered
        # entries to ``.agent/raw/<safe_id>.rendered.log`` and
        # disables the writer so a follow-up ``_emit_activity_event``
        # for the same unit id (a re-spawned worker) does not double-
        # flush an already-disabled instance. The actual pop
        # happens after flush so the writer is reachable for its
        # own flush call.
        writer = self._rendered_writers.pop(unit_id, None)
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.flush()
            with contextlib.suppress(Exception):
                writer.disable()
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
    return cast(
        "PipelineSubscriber | None", getattr(display, "subscriber", None)
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)


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
