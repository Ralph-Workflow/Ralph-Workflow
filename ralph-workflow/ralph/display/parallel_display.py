from __future__ import annotations

import collections
import contextlib
import json
import queue
import re
import sys
import textwrap
import threading
import time
import uuid
import zlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from rich.style import Style as _RichStyle

from rich.cells import cell_len
from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.text import Text as _RichText

from ralph.display import theme as _display_theme
from ralph.display._activity_line_options import ActivityLineOptions as _ActivityLineOptions
from ralph.display._frequency_tier import FrequencyTier
from ralph.display._phase_close_counters import _PhaseCloseCounters
from ralph.display._phase_close_options import PhaseCloseOptions
from ralph.display._phase_counters import PhaseCounters as _PhaseCounters
from ralph.display._plain_constants import (
    _EMPTY_PLAN_SIGNATURE,
    _KIND_TO_TAG,
    _STREAMING_BLOCK_TAGS,
    _STREAMING_KINDS,
    LEVELS,
    _sanitize,
)
from ralph.display._salience import (
    AllocationDecision,
    RoleBid,
    SalienceAllocator,
    demote_hex,
    resolve_color_depth,
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
from ralph.display.capability_observation import CapabilityObservation
from ralph.display.capability_observation_recorder import (
    CapabilityObservationRecorder,
    capability_for_render,
    infer_surface_for_preview,
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
from ralph.display.raw_overflow import (
    DEFAULT_MAX_OVERFLOW_FILE_BYTES,
    RawOverflowLog,
    raw_log_path_for,
)
from ralph.display.record_writer import _INDENT_WIDTH, RenderedRecordWriter
from ralph.display.subscriber import PipelineSubscriber
from ralph.display.theme import (
    diff_fill_styles,
    identity_color,
    pick_status_styles,
)
from ralph.mcp.artifacts.commit_message import read_commit_message_artifact
from ralph.mcp.artifacts.handoffs import handoff_path_for_artifact
from ralph.policy.resolution_names import RESOLUTION_DRAIN_NAME
from ralph.recovery.retry_prompt import VALIDATION_FAILURE_BANNER

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
_SALIENCE_DECISIONS_MAXLEN: int = 1000
SALIENCE_STATE_TOKEN_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "_build_line": ("line", "level"),
    "_activity_text": ("activity", "kind", "unit_id"),
}
_SECONDS_PER_MINUTE: int = 60
_PREVIEW_MAX_LINES: int = 40

_MIN_COALESCE_REPEAT = 2
_MIN_TOOL_RESULT_COLLAPSE_COUNT = 3
_TOOL_RESULT_CHANNEL_RE = re.compile(r"\[tool-result\]\s*")
_TOOL_RESULT_RUNNING_RE = re.compile(r"\s*\(running\.\.\.\)")
_TOOL_RESULT_REPEATED_SEVERITY_AND_IDENTITY_RE = re.compile(r"\b(severity=\S+)\s+\1\s+(\S+)\s+\2\b")


def _clean_tool_result_content(content: str, unit_id: str) -> str:
    clean = _TOOL_RESULT_CHANNEL_RE.sub("", content)
    clean = _TOOL_RESULT_RUNNING_RE.sub("", clean)
    clean = _TOOL_RESULT_REPEATED_SEVERITY_AND_IDENTITY_RE.sub(r"\1 \2", clean)
    if unit_id:
        clean = re.sub(rf"\b{re.escape(unit_id)}(?:\s+{re.escape(unit_id)})+\b", unit_id, clean)
    return " ".join(clean.split())


def _strip_control_chars_for_render(text: str) -> str:
    return _sanitize(text).replace("\n", " ").replace("\t", " ")


def _render_unit_id(unit_id: str) -> str:
    sanitized = _strip_control_chars_for_render(unit_id)
    if len(sanitized) <= _MAX_RENDERED_UNIT_ID_CHARS:
        return sanitized
    return f"{sanitized[: _MAX_RENDERED_UNIT_ID_CHARS - 3]}..."


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

_PHASE_STYLES: dict[str, str] = {
    "execution": "theme.phase.development",
    "analysis": "theme.phase.development_analysis",
    "review": "theme.phase.review",
    "commit": "theme.phase.commit",
    "fix": "theme.phase.fix",
    "verification": "theme.phase.development_analysis",
    "terminal": "theme.phase.complete",
    "fanout_join": "theme.phase.development",
    RESOLUTION_DRAIN_NAME: "theme.phase.fix",
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


MAJOR_ROLE_PAIRS = _MAJOR_ROLE_PAIRS

_INVENTORY_TABLE_COLUMNS = 4
_PROBE_TABLE_COLUMNS = 5
_SERVERS_TABLE_COLUMNS = 5


def _phase_style(phase: str, pipeline_policy: PipelinePolicy | None = None) -> str:
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
    return phase.replace("_", " ").title()


phase_style = _phase_style
phase_label = _phase_label


def phase_style_for_phase(
    phase: str,
    pipeline_policy: PipelinePolicy | None = None,
) -> str:
    return _phase_style(phase, pipeline_policy)


def _resolve_transition_meta(
    from_phase: str,
    to_phase: str,
    pipeline_policy: PipelinePolicy | None,
) -> bool:
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


resolve_transition_meta = _resolve_transition_meta


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
    return ParallelDisplay.strip_markup(line)


def _strip_markup(line: str) -> str:
    return strip_markup_safe(line)


def _tool_preview_lookup_name(source_content: str) -> str:
    return source_content.removeprefix("↳ ").removesuffix(" ↳")


def _record_tool_call_body(record_body: str, source_content: str) -> str:
    if source_content.rstrip().endswith(" ↳") and "↳" not in record_body:
        return f"{record_body} ↳".strip()
    return record_body


_BODY_SEMANTIC_KINDS: frozenset[str] = frozenset(
    {"error", "warning", "tool_use", "tool_result", "thinking"}
)


def _extract_terminal_theme(ctx: object) -> tuple[bool | None, str | None]:
    if isinstance(ctx, DisplayContext):
        return ctx.terminal_background_is_light, ctx.terminal_background_hex
    return None, None


class ParallelDisplay:
    __slots__ = (
        "_active_block",
        "_active_block_chars",
        "_activity_router",
        "_block_open_mono",
        "_block_open_wall",
        "_capability_recorder",
        "_clock",
        "_condensed_logs",
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
        "_last_emitted_status_lines",
        "_last_emitted_tool_signature",
        "_last_phase",
        "_last_phase_artifact_outcome",
        "_last_phase_elapsed_seconds",
        "_last_phase_per_unit",
        "_last_phase_saved_counters",
        "_last_plan_signature",
        "_last_recorded_body",
        "_last_status_lines",
        "_last_status_message",
        "_last_text_thinking_block_close",
        "_last_tool_result_content",
        "_last_waiting_signature",
        "_last_worker_states",
        "_monotonic",
        "_overflow_warned",
        "_pending_phase_headers",
        "_pending_tool_results",
        "_phase_close_emitted",
        "_phase_counters",
        "_recorded_tool_call_ids",
        "_rendered_writers",
        "_run_counters",
        "_run_start_time",
        "_salience_allocator",
        "_salience_decisions",
        "_salience_last_token",
        "_salience_lit_event_roles",
        "_status_bar",
        "_subscriber",
        "_terminal_background_hex",
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
        capability_recorder: CapabilityObservationRecorder | None = None,
    ) -> None:
        if not hasattr(display_context, "console"):
            raise TypeError("display_context is required")
        self._ctx = display_context
        self._is_quiet: bool = is_quiet
        self._terminal_bg_is_light, self._terminal_background_hex = _extract_terminal_theme(
            display_context
        )
        self._capability_recorder: CapabilityObservationRecorder = (
            capability_recorder
            if capability_recorder is not None
            else CapabilityObservationRecorder()
        )
        self._salience_allocator = SalienceAllocator()
        self._salience_last_token: dict[str, tuple[str, ...]] = {}
        self._salience_lit_event_roles: dict[str, bool] = {}
        self._salience_decisions: collections.deque[AllocationDecision] = collections.deque(
            maxlen=_SALIENCE_DECISIONS_MAXLEN
        )
        self._clock: Callable[[], datetime] = (
            clock if clock is not None else (lambda: datetime.now(UTC))
        )
        self._monotonic: Callable[[], float] = (
            monotonic if monotonic is not None else time.monotonic
        )
        self._watchdog_attention: str | None = None
        self._watchdog_attention_lock = threading.Lock()

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
                str | None,
                int,
            ]
            | None
        ) = None
        self._last_analysis_signature: tuple[str | None, str | None, str | None] | None = None
        self._last_waiting_signature: str | None = None
        self._last_status_message: str | None = None
        self._last_emitted_status_lines: dict[str, str] = {}
        self._last_status_lines: dict[str, str] = {}
        self._active_block: dict[str, tuple[str, list[str]]] = {}
        self._active_block_chars: dict[str, int] = {}
        self._block_open_wall: dict[str, datetime] = {}
        self._block_open_mono: dict[str, float] = {}
        self._last_checkpoint_chars: dict[str, int] = {}
        self._emitted_empty_plan = self._emitted_empty_activity = (
            self._emitted_empty_decision_log
        ) = False
        self._phase_counters: _PhaseCounters | None = None
        self._last_phase_saved_counters: _PhaseCounters | None = None
        self._last_phase_elapsed_seconds: float = 0.0
        self._last_phase_artifact_outcome: str = ""
        self._phase_close_emitted: bool = False
        self._run_start_time: float | None = None
        self._run_counters: _PhaseCounters = _PhaseCounters()
        self._last_emitted_tool_signature: dict[str, tuple[str, str, str, int | None]] = {}
        self._last_recorded_body: dict[str, tuple[ActivityEventKind, str]] = {}
        self._recorded_tool_call_ids: dict[str, tuple[str, ...]] = {}
        self._last_tool_result_content: dict[str, tuple[str | None, str]] = {}
        self._pending_tool_results: dict[
            str, tuple[str, dict[str, object], str | None, str | None, float, int]
        ] = {}
        self._last_text_thinking_block_close: dict[str, tuple[ActivityEventKind, str]] = {}

        self._last_phase_per_unit: dict[str, tuple[str, int | None, str | None]] = {}

        self._workspace_root: Path = workspace_root if workspace_root is not None else Path.cwd()

        self._init_overflow_registries()
        self._overflow_warned: set[str] = set()
        self._drop_last_warned: dict[str, float] = {}

        self._rendered_writers: dict[str, RenderedRecordWriter] = {}

        self._pending_phase_headers: list[dict[str, object]] = []

        self._activity_router: ActivityRouter = ActivityRouter(
            on_event=self._on_activity_router_event,
            raw_overflow_callback=self._raw_overflow_write,
        )

        from ralph.display.status_bar import StatusBar

        self._status_bar: StatusBar = StatusBar(self)

        if subscriber is not None:
            self._subscriber = subscriber
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

    @property
    def salience_decisions(self) -> tuple[AllocationDecision, ...]:
        return tuple(self._salience_decisions)

    def _format_timestamp(self, ts: datetime) -> str:
        return ts.strftime("%H:%M:%S")

    @staticmethod
    def _format_hh_mm_ss(ts: datetime) -> str:
        return ts.strftime("%H:%M:%S")

    def _apply_salience(
        self,
        role: str,
        style_str: str,
        *,
        state_token: tuple[str, ...],
    ) -> str:
        depth = resolve_color_depth(self._console)
        if depth == "none":
            return style_str
        previous_token = self._salience_last_token.get(role)
        state_changed = previous_token != state_token
        self._salience_last_token[role] = state_token
        pending = tuple(
            RoleBid(other_role, False)
            for other_role, lit in self._salience_lit_event_roles.items()
            if lit and other_role != role
        )
        bids = (*pending, RoleBid(role, state_changed))
        decisions = self._salience_allocator.allocate_frame(bids, depth=depth)
        for decision in decisions:
            if decision.tier is FrequencyTier.EVENT:
                self._salience_lit_event_roles[decision.role] = decision.lit
            self._salience_decisions.append(decision)
        decision = next(d for d in decisions if d.role == role)
        if decision.lit:
            return style_str
        prefix, _, hex_part = style_str.rpartition(" ")
        demoted_hex = demote_hex(hex_part, surface_hex=self._terminal_background_hex)
        return f"{prefix} {demoted_hex}" if prefix else demoted_hex

    def _build_line(
        self,
        timestamp: str,
        level: str,
        cat: str,
        suffix: str,
        *,
        leading_indent: str = "",
    ) -> Text:
        statuses = pick_status_styles(
            self._terminal_bg_is_light, surface_hex=self._terminal_background_hex
        )
        state = {
            "ERROR": "error",
            "WARN": "warning",
            "SUCCESS": "success",
            "MILESTONE": "running",
        }.get(level, "info")
        timestamp_style = _display_theme._fresh_style(statuses["info"][0])
        suffix_style = _display_theme._fresh_style(
            self._apply_salience(state, statuses[state][0], state_token=("line", level))
        )
        t = Text()
        if leading_indent:
            t.append(leading_indent)
        t.append(timestamp, style=timestamp_style)
        t.append(" ")
        t.append(suffix, style=suffix_style)
        del cat
        return t

    def _activity_text(
        self,
        timestamp: str,
        tag: str,
        unit_id: str,
        body: str,
        *,
        kind: str,
        leading_indent: str = "",
        body_style: str | None = None,
    ) -> Text:
        statuses = pick_status_styles(
            self._terminal_bg_is_light, surface_hex=self._terminal_background_hex
        )
        state = {
            "error": "error",
            "warning": "warning",
            "tool_use": "running",
            "tool_result": "success",
            "thinking": "pending",
        }.get(kind, "info")
        state_style = _display_theme._fresh_style(
            self._apply_salience(state, statuses[state][0], state_token=("activity", kind, unit_id))
        )
        chrome_style = _display_theme._fresh_style(statuses["info"][0])
        if kind in _BODY_SEMANTIC_KINDS:
            body_default_style = state_style
        else:
            disp_styles = _display_theme.display_styles_for_background(
                self._terminal_bg_is_light, surface_hex=self._terminal_background_hex
            )
            body_default_style = _display_theme._fresh_style(disp_styles["agent_text"])
        unit_style = _display_theme._fresh_style(
            f"bold {
                identity_color(
                    unit_id,
                    terminal_bg_is_light=self._terminal_bg_is_light,
                    surface_hex=self._terminal_background_hex,
                )
            }"
        )
        body_style_obj: _RichStyle | str | None
        if isinstance(body_style, str) and not body_style.startswith("theme."):
            body_style_obj = _display_theme._fresh_style(body_style)
        else:
            body_style_obj = body_style
        text = Text(leading_indent)
        text.append(timestamp, style=chrome_style)
        text.append(" ")
        text.append(f"[{tag}]", style=state_style)
        text.append(f"[{unit_id}]", style=unit_style)
        text.append(" ")
        text.append(body, style=body_style_obj or body_default_style)
        return text

    def _close_hang_prefix(self, timestamp: str, tag: str, unit_id: str) -> str:
        return f"{timestamp} {''.ljust(len(f'[{tag}]') + len(unit_id) + 2)}"

    @staticmethod
    def _wrap_close_body(
        header: str,
        visible: str,
        *,
        total_width: int,
        hang_prefix: str = "",
        chrome_prefix_width: int = 0,
    ) -> list[str]:
        first_line = f"{header} {visible}" if visible else header
        if not total_width or cell_len(first_line) + chrome_prefix_width <= total_width:
            return [first_line]
        trailer_idx = visible.rfind("(truncated,")
        if trailer_idx > 0:
            return ParallelDisplay._wrap_close_body_with_trailer(
                header,
                visible,
                trailer_idx=trailer_idx,
                total_width=total_width,
                hang_prefix=hang_prefix,
                chrome_prefix_width=chrome_prefix_width,
            )
        first_budget = max(0, total_width - cell_len(f"{header} ") - chrome_prefix_width)
        cont_budget = max(7, total_width - cell_len(hang_prefix))
        if cell_len(visible) <= first_budget:
            return [first_line]
        if cell_len(visible) <= cont_budget:
            return [header, visible]
        words = visible.split(" ")
        first_words: list[str] = []
        if first_budget > 0:
            while words and cell_len(" ".join([*first_words, words[0]])) <= first_budget:
                first_words.append(words.pop(0))
        if first_words:
            first_chunk = f"{header} {' '.join(first_words)}"
            remaining = " ".join(words)
        else:
            first_chunk = header
            remaining = visible
        continuation_rows = ParallelDisplay._wrap_trailing_words(remaining, total_width=cont_budget)
        return [first_chunk, *continuation_rows]

    @staticmethod
    def _wrap_close_body_with_trailer(
        header: str,
        visible: str,
        *,
        trailer_idx: int,
        total_width: int,
        hang_prefix: str,
        chrome_prefix_width: int,
    ) -> list[str]:
        head = visible[:trailer_idx].rstrip()
        trailer = visible[trailer_idx:]
        first_line_with_head = f"{header} {head}"
        if cell_len(first_line_with_head) + chrome_prefix_width <= total_width:
            return [first_line_with_head, trailer]
        cont_budget = max(7, total_width - cell_len(hang_prefix))
        first_budget = max(0, total_width - cell_len(f"{header} ") - chrome_prefix_width)
        head_words = head.split(" ")
        first_head_words: list[str] = []
        if first_budget > 0:
            while (
                head_words
                and cell_len(" ".join([*first_head_words, head_words[0]])) <= first_budget
            ):
                first_head_words.append(head_words.pop(0))
        if first_head_words:
            first_row = f"{header} {' '.join(first_head_words)}"
            remaining_head = " ".join(head_words)
        else:
            first_row = header
            remaining_head = head
        head_cont_rows = ParallelDisplay._wrap_trailing_words(
            remaining_head, total_width=cont_budget
        )
        trailer_rows = ParallelDisplay._split_long_trailer(trailer, total_width=cont_budget)
        return [first_row, *head_cont_rows, *trailer_rows]

    @staticmethod
    def _wrap_trailing_words(remaining: str, *, total_width: int) -> list[str]:
        if not remaining:
            return []
        rows: list[str] = []
        budget = max(7, total_width)
        while remaining:
            if cell_len(remaining) <= budget:
                rows.append(remaining)
                break
            head_words: list[str] = []
            tail_words = remaining.split(" ")
            while tail_words and cell_len(" ".join([*head_words, tail_words[0]])) <= budget:
                head_words.append(tail_words.pop(0))
            if head_words:
                rows.append(" ".join(head_words))
                remaining = " ".join(tail_words)
                continue
            first_token = tail_words[0]
            if cell_len(first_token) > budget * 2:
                fold_chars: list[str] = []
                folded = ""
                for ch in first_token:
                    if cell_len(folded + ch) > budget and folded:
                        fold_chars.append(folded)
                        folded = ch
                    else:
                        folded += ch
                if folded:
                    fold_chars.append(folded)
                rows.extend(fold_chars[:-1])
                tail_words = tail_words[1:]
                remaining = " ".join([fold_chars[-1], *tail_words]).strip()
                continue
            rows.append(first_token)
            tail_words = tail_words[1:]
            remaining = " ".join(tail_words)
        return rows

    @staticmethod
    def _split_long_trailer(trailer: str, *, total_width: int) -> list[str]:
        return [trailer]

    @staticmethod
    def _wrap_body_with_hanging_indent(
        prefix: str,
        body: str,
        *,
        total_width: int,
        body_measure: int,
    ) -> str:
        if not body:
            return body
        prefix_width = cell_len(prefix)
        budget_terminal = total_width - prefix_width
        budget_measure = body_measure - prefix_width
        budget = max(7, min(budget_terminal, budget_measure))
        if budget <= 0 or cell_len(body) <= budget:
            return body

        def split_to_cells(value: str) -> list[str]:
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
        parts: list[str] = []
        if orientation.developer_agent is not None:
            parts.append(f"developer={strip_markup(orientation.developer_agent)}")
        if orientation.developer_model is not None:
            parts.append(f"model={strip_markup(orientation.developer_model)}")
        return parts

    @classmethod
    def strip_markup(cls, line: str) -> str:
        line = _strip_markup(line)
        return strip_terminal_control(line)

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
        if options is None:
            options = _ActivityLineOptions(
                condensed_ref=condensed_ref,
                condensed_flag=condensed_flag,
                summary_line=summary_line,
                ai_summary_line=ai_summary_line,
                tool_signature=tool_signature,
            )
        opts = options
        if source_timestamp is not None:
            timestamp = _format_iso_timestamp_hh_mm_ss(source_timestamp)
        else:
            timestamp = self._format_timestamp(self._clock())
        rendered_unit_id = _render_unit_id(unit_id)
        base_tag = _KIND_TO_TAG.get(kind, "content")
        sanitized = _sanitize(content) if kind == "raw" else strip_markup(content)
        if opts.condensed_ref is not None and opts.condensed_flag:
            sanitized = f"{sanitized} [see {opts.condensed_ref}]"

        if self._route_streaming(unit_id, kind, content, base_tag, timestamp) is False:
            return

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

        self._emit_activity_supplements(unit_id, timestamp, base_tag, opts)

        if not self._is_quiet:
            chrome_prefix = f"{timestamp} "
            display_tag = {"content": "output", "think": "reasoning"}.get(base_tag, base_tag)
            badge_prefix = f"[{display_tag}][{rendered_unit_id}] "
            indent_level: int = max(0, int(cast("int", getattr(opts, "indent_level", 0))))
            grouping_role: str = str(opts.grouping_role) if opts.grouping_role else "agent_text"
            del grouping_role
            level_indent = " " * (_INDENT_WIDTH * indent_level)
            full_chrome_prefix = chrome_prefix + badge_prefix
            effective_prefix_for_wrap = level_indent + full_chrome_prefix
            wrap_target = body_text if body_text is not None else sanitized
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
                self._activity_text(
                    timestamp,
                    display_tag,
                    rendered_unit_id,
                    first_chunk,
                    kind=kind,
                    leading_indent=level_indent,
                    body_style="theme.display.elision" if opts.condensed_flag else None,
                ),
                markup=False,
                highlight=False,
                no_wrap=True,
                overflow="ignore",
            )
            for chunk in chunks[1:]:
                continuation_body = chunk + (" ↳" if kind == "tool_use" else "")
                self._console.print(
                    self._activity_text(
                        timestamp,
                        display_tag,
                        rendered_unit_id,
                        continuation_body,
                        kind=kind,
                        leading_indent=level_indent,
                        body_style="theme.display.elision" if opts.condensed_flag else None,
                    ),
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
        if kind == "raw":
            return
        if kind == ActivityEventKind.SUBAGENT_PROGRESS:
            return
        try:
            event_kind = ActivityEventKind(kind)
        except ValueError:
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
        if self._is_quiet:
            return
        self.emit_activity_line(unit_id, "raw", line)

    def _emit_carrier_rows(self, unit_id: str, tag: str, body: str, *, kind: str) -> None:
        timestamp = self._format_timestamp(self._clock())
        rendered_unit_id = _render_unit_id(unit_id)
        prefix = f"{timestamp} [{tag}][{rendered_unit_id}] "
        rows = self._wrap_body_with_hanging_indent(
            prefix,
            body,
            total_width=self._ctx.width,
            body_measure=self._ctx.width,
        ).split("\n")
        for row in rows:
            self._console.print(
                self._activity_text(timestamp, tag, rendered_unit_id, row, kind=kind),
                markup=False,
                highlight=False,
                no_wrap=True,
                overflow="ignore",
            )

    def emit_status_line(self, unit_id: str, status: str) -> None:
        if self._is_quiet:
            return
        sanitized_status = _strip_control_chars_for_render(status)
        if self._last_status_lines.get(unit_id) == sanitized_status:
            return
        self._last_status_lines[unit_id] = sanitized_status
        self._emit_carrier_rows(
            unit_id,
            "status",
            sanitized_status,
            kind="status",
        )

    def emit_warn_line(self, unit_id: str, tag: str, message: str) -> None:
        self._emit_carrier_rows(
            unit_id,
            _strip_control_chars_for_render(tag),
            _strip_control_chars_for_render(message),
            kind="warning",
        )

    def _update_counters(self, kind: str, is_new_block: bool) -> None:
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
        opts: _ActivityLineOptions,
    ) -> None:
        rendered_unit_id = _render_unit_id(unit_id)
        if opts.summary_line is not None:
            if opts.summary_line:
                summary_text = _sanitize(opts.summary_line)
                self._console.print(
                    self._build_line(
                        timestamp,
                        "INFO",
                        "",
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
                        "",
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
                    "",
                    f"[{tag}][{rendered_unit_id}] \u21b3 ai-summary: {ai_text}",
                ),
                markup=False,
                highlight=False,
                no_wrap=False,
                overflow="fold",
            )

    def _close_block(self, unit_id: str, timestamp: str) -> None:
        if unit_id not in self._active_block:
            return
        rendered_unit_id = _render_unit_id(unit_id)
        base_tag, accumulated = self._active_block.pop(unit_id)
        self._last_checkpoint_chars.pop(unit_id, None)
        self._active_block_chars.pop(unit_id, None)
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
        overflow = self._get_condensed_log(unit_id)
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
                return
        start_str = self._format_hh_mm_ss(open_wall) if open_wall is not None else "??:??:??"
        end_str = self._format_hh_mm_ss(end_wall)
        if open_mono is not None:
            duration_str = format_elapsed_seconds(end_mono - open_mono)
        else:
            duration_str = format_elapsed_seconds(0.0)
        display_tag = {"content": "output", "think": "reasoning"}.get(base_tag, base_tag)
        body = f"\u22ef {display_tag} \u00b7 {start_str} \u2192 {end_str} \u00b7 {duration_str} \u00b7 {visible}"
        if not self._is_quiet:
            header = f"⋯ {display_tag} · {start_str} → {end_str} · {duration_str}"
            hang_prefix = f"{timestamp} [{display_tag}][{rendered_unit_id}] "
            chrome_prefix_width = cell_len(hang_prefix)
            wrapped_rows = self._wrap_close_body(
                header,
                visible,
                total_width=self._ctx.width,
                hang_prefix=hang_prefix,
                chrome_prefix_width=chrome_prefix_width,
            )
            if wrapped_rows:
                first_chunk, *continuations = wrapped_rows
                self._console.print(
                    self._activity_text(
                        timestamp,
                        display_tag,
                        rendered_unit_id,
                        first_chunk,
                        kind="thinking" if base_tag == "think" else "text",
                    ),
                    markup=False,
                    highlight=False,
                    no_wrap=True,
                    overflow="ignore",
                )
                for chunk in continuations:
                    is_trailer = "(truncated," in chunk
                    self._console.print(
                        self._activity_text(
                            timestamp,
                            display_tag,
                            rendered_unit_id,
                            chunk,
                            kind="thinking" if base_tag == "think" else "text",
                        ),
                        markup=False,
                        highlight=False,
                        no_wrap=True,
                        overflow="ignore",
                        crop=not is_trailer,
                    )
        _base_tag_to_kind = {
            "content": ActivityEventKind.TEXT,
            "think": ActivityEventKind.THINKING,
        }
        record_kind = _base_tag_to_kind.get(base_tag, ActivityEventKind.TEXT)
        self._append_recorded_entry(
            unit_id,
            event_kind=record_kind,
            body=body,
            timestamp=timestamp,
        )
        if record_kind in (ActivityEventKind.TEXT, ActivityEventKind.THINKING):
            self._last_text_thinking_block_close[unit_id] = (record_kind, visible)

    def flush_blocks(self) -> None:
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
        self._active_block[ctx.unit_id] = (ctx.base_tag, [ctx.content])
        self._last_checkpoint_chars[ctx.unit_id] = 0
        self._active_block_chars[ctx.unit_id] = len(ctx.content)
        self._block_open_wall[ctx.unit_id] = self._clock()
        self._block_open_mono[ctx.unit_id] = self._monotonic()
        self._update_counters(ctx.kind, is_new_block=True)
        return None

    def _continue_streaming_block(
        self,
        ctx: _StreamingCtx,
        accumulated: list[str],
        continue_tag: str,
        start_tag: str,
    ) -> tuple[str, str | None] | None:
        if self._ctx.streaming_dedup_enabled and accumulated and accumulated[-1] == ctx.content:
            return None
        if len(accumulated) >= _MAX_STREAMING_FRAGMENTS:
            self._close_block(ctx.unit_id, ctx.timestamp)
            self._handle_new_streaming_block(ctx, start_tag)
            return None
        accumulated.append(ctx.content)
        running_total = self._active_block_chars.get(ctx.unit_id, 0) + len(ctx.content)
        self._active_block_chars[ctx.unit_id] = running_total
        return None

    def _process_streaming_block(
        self,
        ctx: _StreamingCtx,
        block_tags: tuple[str, str],
    ) -> tuple[str, str | None] | None:
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
        for text in self._snapshot_texts(snapshot):
            self._console.print(text, markup=False, highlight=False, no_wrap=True)

    @property
    def _plain_renderer(self) -> ParallelDisplay:
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
        return self._run_start_time

    @property
    def watchdog_attention(self) -> str | None:
        with self._watchdog_attention_lock:
            return self._watchdog_attention

    def set_watchdog_attention(self, value: str | None) -> None:
        with self._watchdog_attention_lock:
            self._watchdog_attention = value

    def _init_overflow_registries(self) -> None:
        self._condensed_logs: dict[str, RawOverflowLog] = {}

    def _get_condensed_log(self, unit_id: str) -> RawOverflowLog:
        from ralph.display.raw_overflow import get_or_create_raw_overflow_log

        if unit_id not in self._condensed_logs:
            self._condensed_logs[unit_id] = get_or_create_raw_overflow_log(
                self._workspace_root,
                unit_id,
                condensed=True,
                max_bytes=_MAX_OVERFLOW_FILE_BYTES,
            )
        return self._condensed_logs[unit_id]

    def _result_preview_target(self, unit_id: str, metadata: dict[str, object]) -> tuple[str, str]:
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
        overflow = self._get_condensed_log(unit_id)
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
            surface_hex=self._terminal_background_hex,
            overflow_ref=overflow_ref,
            glyphs_enabled=self._ctx.glyphs_enabled,
            diff_fills=diff_fill_styles(
                self._terminal_bg_is_light, surface_hex=self._terminal_background_hex
            ),
        )
        if preview is None:
            return
        preview_input_dict = preview_input.get("input")
        operation = "syntax_preview"
        if isinstance(preview_input_dict, dict):
            from ralph.display.preview_payload import payload_from_tool_event as _pfe

            canonical = _pfe(tool_name, preview_input)
            if canonical is not None:
                operation = infer_surface_for_preview(preview, canonical.operation)
        capability = capability_for_render(surface_name=operation, tool_name=tool_name)
        if capability is not None:
            self._capability_recorder.record(
                CapabilityObservation(capability=capability, tool_name=tool_name, unit_id=unit_id)
            )
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
        if unit_id not in self._rendered_writers:
            self._rendered_writers[unit_id] = RenderedRecordWriter(self._workspace_root, unit_id)
            self._flush_pending_phase_headers(unit_id)
        return self._rendered_writers[unit_id]

    def _flush_pending_phase_headers(self, unit_id: str) -> None:
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
        if not body.strip() or (event_kind is ActivityEventKind.LIFECYCLE and phase is None):
            return
        writer = self._get_rendered_writer(unit_id)
        if writer is None:
            return
        prev = self._last_recorded_body.get(unit_id)
        if prev is not None:
            prev_kind, prev_body = prev
            if prev_kind == event_kind and prev_body == body:
                pass
            elif (
                prev_body == body
                and prev_kind in (ActivityEventKind.TEXT, ActivityEventKind.THINKING)
                and event_kind in (ActivityEventKind.TEXT, ActivityEventKind.THINKING)
            ):
                return
            elif prev_kind != event_kind and prev_body == body:
                pass
        from ralph.display.agent_event_renderer import make_event_for_emit
        from ralph.display.presented_entry import build_presented_entry

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

        overflow = self._get_condensed_log(unit_id)
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
        timestamp = self._format_timestamp(self._clock())
        body = transition
        del agent_name
        for unit_id in self._rendered_writers:
            self._last_phase_per_unit[unit_id] = (phase, cycle, iter_)
        if not self._rendered_writers:
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
        overflow = self._get_condensed_log(unit_id)
        overflow.append(raw_line)
        self._check_overflow_size(unit_id, overflow)

    def _check_overflow_size(self, unit_id: str, overflow: RawOverflowLog) -> None:
        warn_key = str(overflow.path)
        if warn_key in self._overflow_warned:
            return
        if overflow.size_bytes >= _MAX_OVERFLOW_FILE_BYTES or overflow.is_disabled:
            self._overflow_warned.add(warn_key)
            overflow.disable()
            self.emit_activity_line(
                unit_id,
                "progress",
                f"\\[overflow log full, {overflow.path.name} for {unit_id} "
                "is no longer being written]",
            )

    def _emit_drop_warning(self, unit_id: str) -> None:
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
        metadata = {} if metadata is None else metadata
        text_content = content or ""

        event = make_event_for_emit(
            kind,
            text_content,
            timestamp=timestamp,
            metadata=metadata,
            source=unit_id,
        )

        if kind is ActivityEventKind.UNKNOWN:
            from ralph.display.presented_entry import build_presented_entry

            if not build_presented_entry(event, unit_id=unit_id).body:
                return

        tool_signature: tuple[str, str] | None = None

        input_dict: dict[str, object] = {}

        if kind is ActivityEventKind.TOOL_USE:
            input_obj = metadata.get("input", metadata.get("args"))
            input_dict = cast("dict[str, object]", input_obj) if isinstance(input_obj, dict) else {}
            original_name = text_content
            tool_path = strip_terminal_control(str(input_dict.get("path", "") or ""))
            tool_workdir = strip_terminal_control(str(input_dict.get("workdir", "") or ""))
            tool_command = strip_terminal_control(str(input_dict.get("command", "") or ""))
            tool_pattern = strip_terminal_control(str(input_dict.get("pattern", "") or ""))
            tool_signature = (original_name, tool_path)
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
            header_metadata = self._preview_header_metadata(original_name, input_dict, metadata)
            if header_metadata is not None:
                event = make_event_for_emit(
                    kind,
                    text_content,
                    timestamp=timestamp,
                    metadata=header_metadata,
                    source=unit_id,
                )

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
            text = text.replace(" ↳ ", " ", 1)
        overflow = self._get_condensed_log(unit_id)
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

        effective_summary_line = None if kind is ActivityEventKind.TOOL_RESULT else summary_line

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
                    surface_hex=self._terminal_background_hex,
                    overflow_ref=overflow_ref,
                    glyphs_enabled=self._ctx.glyphs_enabled,
                    diff_fills=diff_fill_styles(
                        self._terminal_bg_is_light, surface_hex=self._terminal_background_hex
                    ),
                )
                is not None
            )
            if previewed_result:
                content_for_emit = f"↳ {result_preview_tool_name}"
        if kind is not ActivityEventKind.SUBAGENT_PROGRESS:
            body_text_for_wrap = (
                content_for_emit
                if previewed_result
                else text_content
                if kind.value in _STREAMING_KINDS
                else visible
            )
            from ralph.display.presented_entry import build_presented_entry

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
                    record_body=(
                        _record_tool_call_body(
                            preview_record_text(
                                _tool_preview_lookup_name(text_content),
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
                source_timestamp=event.timestamp or None,
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

        self._emit_drop_warning(unit_id)

    @property
    def activity_router(self) -> ActivityRouter:
        return self._activity_router

    @property
    def subscriber(self) -> PipelineSubscriber:
        return self._subscriber

    def start(self) -> None:
        self._status_bar.start()

    def stop(self) -> None:
        with contextlib.suppress(Exception):
            self._status_bar.stop()
        self.flush_blocks()
        self._flush_pending_tool_results()
        self.flush_run_end_writers()

    @property
    def capability_recorder(self) -> CapabilityObservationRecorder:
        return self._capability_recorder

    def flush_run_end_writers(self) -> None:
        for writer in self._rendered_writers.values():
            with contextlib.suppress(Exception):
                writer.flush()
            with contextlib.suppress(Exception):
                writer.disable()
        self._rendered_writers.clear()
        for overflow in self._condensed_logs.values():
            with contextlib.suppress(Exception):
                overflow.flush()

    def update_status_bar(self, model: object) -> None:
        from ralph.display.status_bar import StatusBarModel

        if not isinstance(model, StatusBarModel):
            model_type = type(model).__name__
            msg = f"update_status_bar requires a StatusBarModel, got {model_type}"
            raise TypeError(msg)
        self._status_bar.update(model)

    @property
    def status_bar(self) -> object:
        return self._status_bar

    @staticmethod
    def _is_status_line(line: str) -> bool:
        status_prefixes = (
            "WAITING:",
            "RECOVERING:",
            "STOPPING:",
            "STATUS:",
            "VALIDATION FAILURE:",
            "Pipeline failed:",
            "Interrupted:",
            "Recovery triggered:",
            "Recovery exhausted:",
        )
        return any(line.startswith(prefix) for prefix in status_prefixes)

    def emit(self, unit_id: str | None, line: str) -> None:
        if self._is_quiet:
            return
        if _is_bare_lifecycle(line):
            return
        sanitized_line = strip_markup(line)
        effective_unit = unit_id or "run"
        if self._is_status_line(sanitized_line):
            if self._last_emitted_status_lines.get(effective_unit) == sanitized_line:
                return
            self._last_emitted_status_lines[effective_unit] = sanitized_line
        if unit_id is not None:
            with contextlib.suppress(Exception):
                self._subscriber.record_activity(
                    unit_id=unit_id,
                    line=sanitized_line,
                    agent_name=unit_id,
                )
        self.emit_log_line(effective_unit, sanitized_line)

    def emit_json_payload(self, payload: Mapping[str, object]) -> None:
        sys.stdout.write(json.dumps(payload, default=str) + "\n")

    def emit_parsed_event(
        self,
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        metadata: dict[str, object],
        timestamp: str | None = None,
    ) -> None:
        if kind is ActivityEventKind.TOOL_RESULT and content is not None:
            clean_content = _clean_tool_result_content(content, unit_id)
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
            overflow = self._get_condensed_log(unit_id)
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
        if (
            kind in (ActivityEventKind.LIFECYCLE, ActivityEventKind.UNKNOWN)
            and content is not None
            and _is_bare_lifecycle(content)
        ):
            return
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
        with contextlib.suppress(Exception):
            self._subscriber.record_analysis(phase, decision, reason)

    def _emit_section_rule(self, tag: str) -> None:
        with contextlib.suppress(Exception):
            self._console.print()
            rule_text = _RichText()
            rule_text.append(self._ctx.glyph_for("rule"), style="theme.banner.border")
            rule_text.append(f" {tag}", style="theme.banner.title")
            self._console.print(rule_text, highlight=False, overflow="ignore")

    def emit_run_start(self, orientation: RunStartOrientation) -> None:
        if self._is_quiet:
            return
        height_constrained = self._ctx.is_height_constrained()
        with contextlib.suppress(Exception):
            if not height_constrained:
                self._emit_section_rule("[run-start]")
            timestamp = self._format_timestamp(self._clock())

            t = _RichText()
            t.append(f"{timestamp} ")
            t.append(
                f"[run-start] {self._ctx.glyph_for('milestone')} ",
                style="theme.banner.ascii",
            )
            t.append("Ralph Workflow run start", style="theme.banner.title")
            self._console.print(t, markup=False, highlight=False, no_wrap=True)

            self._emit_run_start(timestamp, orientation, height_constrained=height_constrained)

    _RUN_START_CHROME_PREFIX_LEN: int = len("00:00:00 INFO META [run-start] ")

    def _emit_run_start(
        self,
        timestamp: str,
        orientation: RunStartOrientation,
        *,
        height_constrained: bool = False,
    ) -> None:
        pw_parts: list[tuple[str, str]] = []
        if orientation.prompt_path is not None:
            pw_parts.append(("prompt", strip_markup(orientation.prompt_path)))
        if orientation.workspace_root is not None:
            pw_parts.append(("workspace", strip_markup(orientation.workspace_root)))
        if orientation.target_worktree_path is not None:
            pw_parts.append(
                (
                    "target-worktree",
                    f"{strip_markup(orientation.target_worktree_path)} "
                    "(volatile; Ralph Workflow may snapshot and reset it)",
                )
            )
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
        self._console.print(
            self._build_line(timestamp, "INFO", "META", "[run-start] orientation"),
            markup=False,
            highlight=False,
            no_wrap=True,
        )
        chrome_prefix_len = self._RUN_START_CHROME_PREFIX_LEN + len("  ")
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
        rendered = f"{key}={value}"
        if cell_len(rendered) <= body_budget:
            line = self._build_line(timestamp, "INFO", "META", f"[run-start]   {rendered}")
            return line
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
        return self._last_phase_elapsed_seconds

    @property
    def last_phase_counters(self) -> _PhaseCounters | None:
        return self._last_phase_saved_counters

    @property
    def last_phase_artifact_outcome(self) -> str:
        return self._last_phase_artifact_outcome

    @property
    def phase_close_emitted(self) -> bool:
        return self._phase_close_emitted

    def record_artifact_outcome(self, outcome: str) -> None:
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
        opts = options or PhaseCloseOptions()
        self.flush_blocks()
        clean_produced = strip_markup(produced).strip()
        counters = self._phase_counters
        if counters is not None:
            elapsed_s = round(max(0.0, self._monotonic() - counters.start_time), 1)
        else:
            elapsed_s = 0.0
            counters = _PhaseCounters()
        if opts.counter_overrides is not None:
            cb = (
                counters.content_blocks
                if opts.counter_overrides.content_blocks is None
                else opts.counter_overrides.content_blocks
            )
            tb = (
                counters.thinking_blocks
                if opts.counter_overrides.thinking_blocks is None
                else opts.counter_overrides.thinking_blocks
            )
            tc = (
                counters.tool_calls
                if opts.counter_overrides.tool_calls is None
                else opts.counter_overrides.tool_calls
            )
            err = (
                counters.errors
                if opts.counter_overrides.errors is None
                else opts.counter_overrides.errors
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
        full_carrier = f"[phase-close] {glyph_prefix}phase={phase}{iter_labels}"
        compact_phase = f"{zlib.crc32(phase.encode('utf-8')) & 0xFFFFFF:06x}"
        compact_carrier = f"[phase-close][{compact_phase}]"
        carrier = (
            full_carrier
            if self._ctx.width - cell_len(full_carrier) >= len("thinking_blocks=34")
            else compact_carrier
        )
        payload_parts = [part for part in (clean_produced, suffix.strip()) if part]
        payload = " ".join(payload_parts)
        prefix = f"{carrier} "
        folded_rows = self._wrap_body_with_hanging_indent(
            prefix,
            payload,
            total_width=self._ctx.width,
            body_measure=self._ctx.width,
        ).split("\n")
        carrier_style = _display_theme._fresh_style(
            pick_status_styles(
                self._terminal_bg_is_light, surface_hex=self._terminal_background_hex
            )["info"][0]
        )
        for row in folded_rows:
            self._console.print(
                Text(f"{carrier} {row}", style=carrier_style),
                markup=False,
                highlight=False,
                no_wrap=True,
            )
        self._last_phase_saved_counters = counters
        self._last_phase_elapsed_seconds = elapsed_s
        self._phase_counters = None

    def emit_phase_close_from_exit(self, exit_model: PhaseExitModel) -> None:
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._last_phase_artifact_outcome = exit_model.artifact_outcome
            self._phase_close_emitted = True
            iter_ctx = exit_model.to_iteration_context()
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
            counter_overrides = _PhaseCloseCounters(
                content_blocks=exit_model.content_blocks,
                thinking_blocks=exit_model.thinking_blocks,
                tool_calls=exit_model.tool_calls,
                errors=exit_model.errors,
            )
            artifact_outcome = (
                VALIDATION_FAILURE_BANNER
                if exit_model.last_failure_category == "artifact_validation"
                else exit_model.artifact_outcome
            )
            self._emit_phase_close_body(
                exit_model.phase_name,
                artifact_outcome,
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
                if exit_model.last_failure_category == "artifact_validation":
                    level = "ERROR"
                    message = (
                        f"[phase-close] {VALIDATION_FAILURE_BANNER} "
                        f"phase={exit_model.phase_name} {' '.join(debug_parts)}"
                    )
                else:
                    level = "WARN"
                    message = (
                        f"[phase-close] debug phase={exit_model.phase_name} {' '.join(debug_parts)}"
                    )
                self._console.print(
                    self._build_line(timestamp, level, "META", message),
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
            self._console.print()

    def emit_completion_summary_panel(
        self,
        snapshot: PipelineSnapshot,
        *,
        options: CompletionSummaryOptions | None = None,
    ) -> None:
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
                if snapshot.is_terminal_failure:
                    self._console.print(Text(f"  failed_phase={snapshot.phase}", style=style))
                    self._console.print(
                        Text(
                            f"  error: {snapshot.last_error or 'unknown failure'}",
                            style="theme.level.error",
                        )
                    )
                if resolved_options.overflow_path is not None:
                    self._console.print(
                        Text(
                            f"  raw_overflow={resolved_options.overflow_path}",
                            style="theme.text.muted",
                        )
                    )
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
                condensed_chars = len(
                    render_completion_summary(snapshot, options=resolved_options).plain
                )
                self._console.print(
                    Text(
                        f"  -- {len(condensed_sections)} sections condensed · "
                        f"{condensed_chars} chars",
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

    def emit_phase_start(
        self,
        phase: str,
        *,
        agent_name: str | None = None,
        pipeline_policy: PipelinePolicy | None = None,
    ) -> None:
        if self._is_quiet:
            return
        self.flush_blocks()
        with contextlib.suppress(Exception):
            self._emit_section_rule("[phase-open]")
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
        if self._is_quiet:
            return
        self.flush_blocks()
        with contextlib.suppress(Exception):
            self._emit_section_rule("[phase-open]")
            c = self._console
            style = _phase_style(entry.phase_name, pipeline_policy)
            label = entry.human_label()
            start_glyph = self._ctx.glyph_for("start")
            od_glyph = self._ctx.glyph_for("outer_dev")
            ia_glyph = self._ctx.glyph_for("inner_analysis")
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
        content_blocks = exit_model.content_blocks or 0
        thinking_blocks = exit_model.thinking_blocks or 0
        tool_calls = exit_model.tool_calls or 0
        errors = exit_model.errors or 0
        total = content_blocks + thinking_blocks + tool_calls + errors
        if total == 0:
            return None
        stats = Text()
        stats.append("    \u21b3 stats: ", style="theme.text.muted")
        parts: list[tuple[str, str]] = [
            (f"content={content_blocks}", "theme.text.muted"),
            (f"thinking={thinking_blocks}", "theme.text.muted"),
            (f"tools={tool_calls}", "theme.text.muted"),
        ]
        if errors > 0:
            parts.append((f"errors={errors}", "theme.level.error"))
        for i, (part_text, part_style) in enumerate(parts):
            if i > 0:
                stats.append("  ", style="theme.text.muted")
            stats.append(part_text, style=part_style)
        return stats

    def _build_review_outcome_line(self, exit_model: PhaseExitModel) -> Text | None:
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
        parts: list[str] = []
        if elapsed_seconds > 0:
            parts.append(format_elapsed_seconds(elapsed_seconds))
        if exit_trigger is not None:
            parts.append(f"{arrow} {exit_trigger}")
        if parts:
            console.print(Rule(title="  ".join(parts), style=style))
        else:
            console.print(Rule(style=style))

    def emit_plan_artifact(self, workspace_root: Path) -> None:
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
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            artifact_type = self._analysis_handoff_artifact_type(drain)
            if artifact_type is not None:
                markdown = self._resolve_authoritative_markdown_handoff(
                    workspace_root,
                    artifact_type,
                )
                if markdown:
                    self._emit_section_rule("[analysis]")
                    self._render_text_block(f"ANALYSIS: {drain}", markdown, "analysis")
                    return
            summary = read_latest_analysis_decision(workspace_root, drain)
            if summary is None:
                return
            self._emit_section_rule("[analysis]")
            lines = [f"  decision: {summary.decision}"]
            if summary.reason:
                lines.append(f"  reason: {summary.reason}")
            self._render_titled_lines(f"ANALYSIS: {drain}", "analysis", lines)

    def emit_commit_message(self, workspace_root: Path) -> None:
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            try:
                message = read_commit_message_artifact(workspace_root)
            except Exception:
                message = None
            if message is None:
                return
            self._emit_section_rule("[commit-message]")
            self._render_text_block("COMMIT MESSAGE", message, "commit", indent=True)

    def emit_missing_plan_hint(self) -> None:
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            timestamp = datetime.now(UTC).isoformat()
            hint = Text(timestamp, style="theme.level.info")
            hint.append(" INFO ", style="theme.level.info")
            hint.append("META [plan] (no plan artifact on disk)", style="theme.cat.meta")
            self._console.print(hint, markup=False, highlight=False, no_wrap=True)

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
                surface_hex=self._terminal_background_hex,
            )
        )
        self._console.print(Rule(style=_phase_style(style_phase)))

    def emit_first_run_panel(self, content: list[RenderableType]) -> None:
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            if self._ctx.is_height_constrained():
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
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[welcome]")
            if self._ctx.is_height_constrained():
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
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[agents]")
            table = Table(
                title="Configured Agents", show_header=True, border_style="theme.panel.border"
            )
            table.add_column("Name", style="theme.cat.meta")
            table.add_column("Command")
            table.add_column("Parser", style="theme.cat.cont")
            if not agents:
                table.add_row(Text("No agents configured", style="theme.text.muted"), "", "")
            else:
                for name, agent in agents.items():
                    cmd = getattr(agent, "cmd", "")
                    parser = getattr(agent, "json_parser", None)
                    parser_str = str(parser.value if parser is not None else "")
                    table.add_row(name, cmd, parser_str)
            self._console.print(table)

    def emit_providers_table(self, providers: list[str]) -> None:
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[providers]")
            table = Table(
                title="Available Providers", show_header=True, border_style="theme.panel.border"
            )
            table.add_column("Provider", style="theme.cat.meta")
            table.add_column("Status", justify="center")
            if not providers:
                table.add_row(Text("No providers available", style="theme.text.muted"), "")
            else:
                for provider in providers:
                    table.add_row(provider, "Available")
            self._console.print(table)

    def emit_config_table(self, config: UnifiedConfig) -> None:
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[config]")
            if self._ctx.is_height_constrained():
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
            table = Table(
                title="Baseline Capabilities", show_header=True, border_style="theme.panel.border"
            )
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
                skill_table = Table(show_header=True, border_style="theme.panel.border")
                skill_table.add_column("Agent", style="theme.cat.meta")
                skill_table.add_column("Skill root", style="theme.text.muted")
                skill_table.add_column("Scope", style="theme.cat.meta")
                skill_table.add_column("Status")
                for agent_label, skill_root, scope, status_text in skill_rows:
                    skill_table.add_row(agent_label, skill_root, scope, status_text)
                self._console.print(skill_table)

    def emit_machine_json(self, payload: str) -> None:
        if self._is_quiet:
            return
        self._console.print(
            strip_terminal_control(payload), markup=False, highlight=False, soft_wrap=True
        )

    def emit_status(self, message: str) -> None:
        if self._is_quiet:
            return
        if self._last_status_message == message:
            return
        self._last_status_message = message
        with contextlib.suppress(Exception):
            self._emit_section_rule("[status]")
            self._console.print(
                Text(f"INFO {strip_terminal_control(message)}", style="theme.status.info"),
                markup=False,
                highlight=False,
            )

    def emit_warning(self, message: str) -> None:
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[warning]")
            self._console.print(
                Text(f"WARN {strip_terminal_control(message)}", style="theme.status.warning"),
                markup=False,
                highlight=False,
                overflow="fold",
            )

    def emit_skill_failure_warning(self, failures: list[str]) -> None:
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
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[next-steps]")
            for index, line in enumerate(next_steps, start=1):
                self._console.print(
                    Text(f"  {index}. {line}", style="theme.text.muted"),
                    markup=False,
                    highlight=False,
                )

    def emit_blank_line(self) -> None:
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._console.print()

    def emit_info_panel(self, *, title: str, content: str) -> None:
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[info]")
            if self._ctx.is_height_constrained():
                self._console.print(
                    Text(title, style="theme.phase.planning"),
                )
                self._console.print(
                    Text(content, style="theme.text.muted"),
                    markup=False,
                    highlight=False,
                    soft_wrap=True,
                    no_wrap=False,
                    overflow="fold",
                    width=self._ctx.body_measure(),
                )
            else:
                panel = Panel(
                    Text(content, style="theme.text.muted"),
                    title=title,
                    border_style="theme.phase.planning",
                    padding=(1, 2),
                    expand=False,
                    width=self._ctx.body_measure(),
                )
                self._console.print(panel)

    def emit_metrics_table(self, metrics: dict[str, int]) -> None:
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
                border_style="theme.panel.border",
            )
            table.add_column("Metric", style="theme.cat.meta")
            table.add_column("Value", justify="right", style="theme.status.success")
            for name, value in metrics.items():
                table.add_row(name, str(value))
            self._console.print(table)

    def emit_checkpoint_summary_table(self, options: object) -> None:
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
                border_style="theme.panel.border",
            )
            table.add_column("Property", style="theme.cat.meta")
            table.add_column("Value")
            table.add_row("Phase", str(phase))
            for counter_name, value_pair in progress.items():
                completed, cap = value_pair
                table.add_row(str(counter_name), f"{completed}/{cap}")
            self._console.print(table)

    def emit_diagnose_inventory_table(self, rows: Sequence[tuple[object, ...]]) -> None:
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[diagnose-inventory]")
            table = Table(
                title="Effective Session MCP Inventory",
                show_header=True,
                title_style="theme.banner.title",
                header_style="theme.text.emphasis",
                border_style="theme.panel.border",
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
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[diagnose-probe]")
            table = Table(
                title="Agent Transport Compatibility",
                show_header=True,
                title_style="theme.banner.title",
                header_style="theme.text.emphasis",
                border_style="theme.panel.border",
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
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._emit_section_rule("[diagnose-servers]")
            table = Table(
                title="Custom MCP Servers",
                show_header=True,
                title_style="theme.banner.title",
                header_style="theme.text.emphasis",
                border_style="theme.panel.border",
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
        if self._is_quiet:
            return
        with contextlib.suppress(Exception):
            self._console.print(renderable)

    @property
    def display_context(self) -> DisplayContext:
        return self._ctx

    @property
    def console(self) -> Console:
        return self._ctx.console

    def drop_unit(self, unit_id: str) -> None:
        self._overflow_warned.discard(
            str(raw_log_path_for(self._workspace_root, unit_id, condensed=True))
        )
        self._drop_last_warned.pop(unit_id, None)
        self._last_emitted_tool_signature.pop(unit_id, None)
        self._last_worker_states.pop(unit_id, None)
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
        self._last_phase_per_unit.pop(unit_id, None)
        overflow = self._condensed_logs.pop(unit_id, None)
        if overflow is not None:
            with contextlib.suppress(Exception):
                overflow.flush()
            with contextlib.suppress(Exception):
                overflow.disable()
            with contextlib.suppress(Exception):
                from ralph.display.raw_overflow import _forget_raw_overflow_log

                _forget_raw_overflow_log(str(overflow.path.resolve(strict=False)))
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
    return ParallelDisplay(display_context)


def resolve_display(
    display: ParallelDisplay | None,
    display_context: DisplayContext | None = None,
    *,
    is_quiet: bool = False,
) -> ParallelDisplay:
    if display is not None:
        return display
    if display_context is None:
        raise TypeError("display_context is required when display is None")
    return ParallelDisplay(display_context, is_quiet=is_quiet)


def status_text(
    label: str,
    value: str,
    style: str,
    *,
    width: int | None = None,
) -> str:
    del style
    raw = f"{label}: {value}"
    if width is None or cell_len(raw) <= width:
        return raw
    indent = " " * (cell_len(label) + 2)
    wrapper = textwrap.TextWrapper(
        width=width,
        initial_indent="",
        subsequent_indent=indent,
        break_long_words=True,
        break_on_hyphens=False,
    )
    return wrapper.fill(raw)


def build_default_display_legacy_bridge(
    workspace_root: Path,
    display_context: DisplayContext,
    pipeline_policy: PipelinePolicy | None = None,
    *,
    is_quiet: bool = False,
) -> ParallelDisplay:
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
