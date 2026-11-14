from __future__ import annotations

import contextlib
import dataclasses
import os
import pathlib
import re
import threading
import time
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, Protocol

from rich.cells import cell_len
from rich.text import Text

from ralph.display.line_sanitizer import strip_terminal_control
from ralph.display.phase_status import (
    format_analysis_cycle,
    format_analysis_cycle_compact,
    format_analysis_cycle_minimal,
    format_dev_cycle,
    format_dev_cycle_compact,
    format_dev_cycle_minimal,
)
from ralph.display.terminal_restore import terminal_understands_vt
from ralph.display.theme import (
    _DISPLAY_IDENTITY_ACTIVE_SET,
    _fresh_style,
    identity_color,
    pick_status_styles,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.live import Live as _Live

    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline._integration_status import IntegrationStatus

    class _StatusBarHost(Protocol):
        _ctx: DisplayContext
        _is_quiet: bool

        @property
        def watchdog_attention(self) -> str | None: ...


_STATUS_BAR_REFRESH_PER_SECOND: float = 4.0
_STATUS_BAR_TRANSIENT: bool = False


DEFAULT_PATH_BUDGET: int = 48
DEFAULT_PHASE_LABEL_BUDGET: int = 28
_HOME_PREFIX: str = "~"
_ELLIPSIS: str = "..."
_ELLIPSIS_LEN: int = len(_ELLIPSIS)
_MIN_BUDGET: int = _ELLIPSIS_LEN + 1

_SAFE_LINE_NEWLINE_RE: re.Pattern[str] = re.compile(r"[\r\n]+")
_SAFE_LINE_CONTROL_RE: re.Pattern[str] = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")


def _safe_single_line(text: str) -> str:
    if not text:
        return ""
    cleaned = strip_terminal_control(text)
    cleaned = _SAFE_LINE_CONTROL_RE.sub(" ", cleaned)
    cleaned = _SAFE_LINE_NEWLINE_RE.sub(" ", cleaned)
    return cleaned.strip()


_OUTER_DEV_LABEL_MAX_CHARS: int = 11
_INNER_ANALYSIS_LABEL_MAX_CHARS: int = 11
_OUTER_DEV_LABEL_COMPACT_MAX_CHARS: int = 6
_INNER_ANALYSIS_LABEL_COMPACT_MAX_CHARS: int = 7
_OUTER_DEV_LABEL_MINIMAL_MAX_CHARS: int = 5
_INNER_ANALYSIS_LABEL_MINIMAL_MAX_CHARS: int = 6
_OUTER_DEV_LABEL_SUFFIX_MAX_CHARS: int = 7
_CANONICAL_FIT_THRESHOLD: int = 120
_AGENT_FIT_THRESHOLD: int = 60
_AGENT_PATH_FIT_THRESHOLD: int = 80
_NARROW_AGENT_PATH_LAYOUT_MAX_WIDTH: int = 85
_AGENT_LABEL_FIXED_WIDTH: int = len("Agent claude")
_ELAPSED_SHORT_FIXED_WIDTH: int = 6

_PATH_DROP_THRESHOLD: int = 60

_PHASE_ABBREVIATE_THRESHOLD: int = 80

_PHASE_ABBREVIATIONS: dict[str, str] = {
    "planning": "Plan",
    "planning_analysis": "PAn",
    "development": "Dev",
    "development_commit_review": "DCr",
    "development_commit": "DCm",
    "development_analysis": "DAn",
    "development_final_commit_review": "FCr",
    "development_final_commit": "FCm",
    "conflict_resolution": "Conf",
    "evidence_diagnosis": "Diag",
    "ladder_escalation": "Diag",
    "intervention_required": "Intv",
    "complete": "DONE",
    "failed_terminal": "FAIL",
    "cancelled": "CXL",
    "integration": "Intg",
    "integrating": "Intg",
    "landing": "Land",
    "publication": "Pub",
    "publishing": "Pub",
    "waiting": "Wait",
    "intervention": "Intv",
}


def _abbreviated_phase_label(label: str, budget: int, width: int) -> str:
    key = label.lower().replace(" ", "_")
    return _tail_truncate(_PHASE_ABBREVIATIONS.get(key, key.title()), budget)


_FLOOR_THRESHOLD: int = 40

_MIN_PHASE_BUDGET: int = 3
_MIN_PATH_BUDGET: int = 6
_MIN_PHASE_PLUS_PATH: int = _MIN_PHASE_BUDGET + _MIN_PATH_BUDGET


ATTENTION_PRESENTATION: dict[str, tuple[str, str, str]] = {
    "starting": ("STARTING", "liveness", "theme.status.info"),
    "waiting": ("WAITING", "waiting", "theme.status.warn"),
    "stalled": ("STALLED", "stalled", "theme.status.error"),
    "retrying": ("RETRYING", "retrying", "theme.status.warn"),
    "terminated": ("DONE", "terminated", "theme.status.info"),
    "completed": ("COMPLETE", "terminated", "theme.status.info"),
    "failed": ("FAILED", "terminated", "theme.status.error"),
    "cancelled": ("CANCELLED", "terminated", "theme.status.warn"),
    "intervention_required": ("INTERVENTION_REQUIRED", "error", "theme.status.error"),
    "intervention": ("INTERVENTION", "error", "theme.status.error"),
    "conflict_resolution": ("CONFLICT_RESOLUTION", "warning", "theme.status.warn"),
    "evidence_diagnosis": ("EVIDENCE_DIAGNOSIS", "info", "theme.status.info"),
    "ladder_escalation": ("EVIDENCE_DIAGNOSIS", "info", "theme.status.info"),
    "integration": ("INTEGRATION", "liveness", "theme.status.info"),
    "integrating": ("INTEGRATING", "liveness", "theme.status.info"),
    "landing": ("LANDING", "liveness", "theme.status.info"),
    "publication": ("PUBLICATION", "liveness", "theme.status.info"),
    "publishing": ("PUBLISHING", "liveness", "theme.status.info"),
}

_EXTENDED_ATTENTION_KEYS: frozenset[str] = frozenset(
    {
        "intervention_required",
        "intervention",
        "conflict_resolution",
        "evidence_diagnosis",
        "ladder_escalation",
        "integration",
        "integrating",
        "landing",
        "publication",
        "publishing",
    }
)

_ATTENTION_PRESENTATION_MAX_LABEL_CELLS: int = max(
    cell_len(label)
    for key, (label, _glyph, _style) in ATTENTION_PRESENTATION.items()
    if key not in _EXTENDED_ATTENTION_KEYS
)


def _attention_slot_reserved_width(ctx: DisplayContext) -> int:
    separator = _field_separator(ctx)
    max_glyph_len = max(
        (
            cell_len(ctx.glyph_for(glyph_key))
            for glyph_key in (
                glyph_key for _label, glyph_key, _style in ATTENTION_PRESENTATION.values()
            )
        ),
        default=0,
    )
    return max_glyph_len + 1 + _ATTENTION_PRESENTATION_MAX_LABEL_CELLS + cell_len(separator)


@dataclass(frozen=True)
class StatusBarModel:
    workspace_root: str
    phase_label: str
    phase_style: str
    outer_dev_iteration: int | None = None
    outer_dev_cap: int | None = None
    inner_analysis: int | None = None
    inner_analysis_cap: int | None = None
    integration_alert: str | None = None
    integration_status: IntegrationStatus | None = None
    outer_label: str | None = None
    elapsed_seconds: float | None = None
    agent_name: str | None = None
    run_started_monotonic: float | None = None
    attention: str | None = None
    operation_display_state: str | None = None


def _home_relative(path: str, home: str | None) -> str:
    if home is None or not home:
        return path
    home_str = str(home)
    if path == home_str:
        return _HOME_PREFIX
    if path.startswith(home_str + os.sep):
        return _HOME_PREFIX + path[len(home_str) :]
    return path


def _slice_to_cells(text: str, budget: int) -> str:
    if budget <= 0:
        return ""
    result: list[str] = []
    used = 0
    for char in text:
        width = cell_len(char)
        if used + width > budget:
            break
        result.append(char)
        used += width
    return "".join(result)


def _middle_truncate_path(path: str, budget: int) -> str:
    if cell_len(path) <= budget:
        return path
    last_sep = path.rfind(os.sep)
    last_segment = path[last_sep + 1 :] if last_sep >= 0 else path
    separator_budget = cell_len(_ELLIPSIS) + 1
    if budget >= cell_len(last_segment) + separator_budget:
        return f".../{last_segment}"
    return _tail_truncate(last_segment, budget)


def _tail_truncate(text: str, budget: int) -> str:
    if cell_len(text) <= budget:
        return text
    ellipsis_width = cell_len(_ELLIPSIS)
    if budget <= ellipsis_width:
        return _slice_to_cells(text, budget)
    return _slice_to_cells(text, budget - ellipsis_width).rstrip() + _ELLIPSIS


def _field_separator(ctx: DisplayContext) -> str:
    return f"{ctx.glyph_for('milestone')} "


def _iteration_segment_width(
    ctx: DisplayContext,
    *,
    glyph_key: str,
    label_max_chars: int,
) -> int:
    separator = _field_separator(ctx)
    glyph = ctx.glyph_for(glyph_key)
    return cell_len(separator) + cell_len(glyph) + 1 + label_max_chars


@dataclass(frozen=True)
class _FieldBudgets:
    phase_budget: int
    path_budget: int
    outer_dev_label_max_chars: int
    inner_analysis_label_max_chars: int
    render_marker: bool
    render_iter_glyph: bool


def _field_overhead_and_label_budgets(
    ctx: DisplayContext,
    *,
    has_outer_dev: bool,
    has_inner_analysis: bool,
    has_agent: bool = False,
    outer_label_canonical_chars: int = _OUTER_DEV_LABEL_MAX_CHARS,
    integration_alert_len: int = 0,
) -> _FieldBudgets:
    separator_len = cell_len(_field_separator(ctx))
    marker_len = cell_len(ctx.glyph_for("phase_marker") + " ") if ctx.glyphs_enabled else 0
    outer_dev_glyph_len = cell_len(ctx.glyph_for("outer_dev"))
    inner_analysis_glyph_len = cell_len(ctx.glyph_for("inner_analysis"))

    def _iter_width(
        outer_label: int,
        inner_label: int,
        with_glyph: bool,
        *,
        include_outer: bool = True,
        include_inner: bool = True,
    ) -> int:
        total = 0
        if has_outer_dev and include_outer:
            total += separator_len + outer_label
            if with_glyph:
                total += outer_dev_glyph_len + 1
        if has_inner_analysis and include_inner:
            total += separator_len + inner_label
            if with_glyph:
                total += inner_analysis_glyph_len + 1
        return total

    def _chrome(
        outer_label: int,
        inner_label: int,
        with_marker: bool,
        with_glyph: bool,
        *,
        include_outer: bool = True,
        include_inner: bool = True,
    ) -> int:
        ml = marker_len if with_marker else 0
        return ml + _iter_width(
            outer_label,
            inner_label,
            with_glyph,
            include_outer=include_outer,
            include_inner=include_inner,
        )

    def _allocate(
        outer_label: int,
        inner_label: int,
        with_marker: bool,
        with_glyph: bool,
        *,
        include_outer: bool = True,
        include_inner: bool = True,
    ) -> _FieldBudgets | None:
        attention_chrome = _attention_slot_reserved_width(ctx)
        if ctx.width >= _CANONICAL_FIT_THRESHOLD:
            elapsed_chrome = _ELAPSED_FIXED_WIDTH + separator_len
        elif ctx.width >= _FLOOR_THRESHOLD:
            elapsed_chrome = _ELAPSED_SHORT_FIXED_WIDTH + separator_len
        else:
            elapsed_chrome = 0
        agent_chrome = (
            separator_len
            + (
                _AGENT_LABEL_FIXED_WIDTH
                if ctx.width < _CANONICAL_FIT_THRESHOLD
                else len("Agent pi · minimax/MiniMax-3")
            )
            if has_agent and ctx.width >= _AGENT_FIT_THRESHOLD
            else 0
        )
        path_chrome = separator_len if ctx.width > _PATH_DROP_THRESHOLD else 0
        available = (
            ctx.width
            - _chrome(
                outer_label,
                inner_label,
                with_marker,
                with_glyph,
                include_outer=include_outer,
                include_inner=include_inner,
            )
            - attention_chrome
            - elapsed_chrome
            - agent_chrome
            - path_chrome
            - integration_alert_len
        )
        if available < _MIN_PHASE_PLUS_PATH:
            return None
        if ctx.width < _PHASE_ABBREVIATE_THRESHOLD:
            phase_budget = _MIN_PHASE_BUDGET
            path_budget = available - phase_budget
        elif available - _MIN_PATH_BUDGET >= DEFAULT_PHASE_LABEL_BUDGET:
            phase_budget = DEFAULT_PHASE_LABEL_BUDGET
            path_budget = available - phase_budget
        else:
            phase_budget = available - _MIN_PATH_BUDGET
            path_budget = _MIN_PATH_BUDGET
        if ctx.width < _PHASE_ABBREVIATE_THRESHOLD:
            phase_budget = min(phase_budget, _MIN_PHASE_BUDGET)
            path_budget = available - phase_budget
        if phase_budget < _MIN_PHASE_BUDGET:
            phase_budget = _MIN_PHASE_BUDGET
            path_budget = available - phase_budget
        if path_budget < _MIN_PATH_BUDGET:
            path_budget = _MIN_PATH_BUDGET
            phase_budget = available - path_budget
        if phase_budget < _MIN_PHASE_BUDGET or path_budget < _MIN_PATH_BUDGET:
            return None
        return _FieldBudgets(
            phase_budget,
            path_budget,
            outer_label if include_outer else 0,
            inner_label if include_inner else 0,
            with_marker,
            with_glyph,
        )

    label_forms: tuple[tuple[int, int], ...] = (
        ((outer_label_canonical_chars, _INNER_ANALYSIS_LABEL_MAX_CHARS),)
        if ctx.width >= _CANONICAL_FIT_THRESHOLD
        else (
            (_OUTER_DEV_LABEL_COMPACT_MAX_CHARS, _INNER_ANALYSIS_LABEL_COMPACT_MAX_CHARS),
            (_OUTER_DEV_LABEL_MINIMAL_MAX_CHARS, _INNER_ANALYSIS_LABEL_MINIMAL_MAX_CHARS),
        )
    )

    iter_bearing_configs: tuple[tuple[bool, bool], ...] = (
        (True, True),
        (True, False),
        (False, True),
    )
    for include_outer, include_inner in iter_bearing_configs:
        for outer_label, inner_label in label_forms:
            for with_marker in (
                (False,)
                if _AGENT_PATH_FIT_THRESHOLD <= ctx.width <= _NARROW_AGENT_PATH_LAYOUT_MAX_WIDTH
                else (True, False)
            ):
                for with_glyph in (
                    (False, True)
                    if _AGENT_PATH_FIT_THRESHOLD <= ctx.width <= _NARROW_AGENT_PATH_LAYOUT_MAX_WIDTH
                    else (True, False)
                ):
                    budget = _allocate(
                        outer_label,
                        inner_label,
                        with_marker,
                        with_glyph,
                        include_outer=include_outer,
                        include_inner=include_inner,
                    )
                    if budget is not None:
                        return budget

    for with_marker in (True, False):
        budget = _allocate(
            _OUTER_DEV_LABEL_MINIMAL_MAX_CHARS,
            _INNER_ANALYSIS_LABEL_MINIMAL_MAX_CHARS,
            with_marker,
            with_glyph=False,
            include_outer=False,
            include_inner=False,
        )
        if budget is not None:
            return budget

    return _FieldBudgets(
        0,
        0,
        0,
        0,
        False,
        False,
    )


def _pad_to_cells(text: str, width: int) -> str:
    return text + " " * max(0, width - cell_len(text))


def _format_dev_label(
    n: int,
    cap: int | None,
    max_chars: int,
    *,
    outer_label: str | None = None,
) -> str:
    if max_chars <= 0:
        return ""
    canonical_needed = _outer_label_canonical_chars(outer_label)
    if max_chars >= canonical_needed:
        label = format_dev_cycle(n, cap)
        if outer_label:
            rendered = f"{outer_label} {label.split(' ', 1)[1]}"
            return _pad_to_cells(_tail_truncate(rendered, max_chars), max_chars)
        return _pad_to_cells(label, max_chars)
    if max_chars >= _OUTER_DEV_LABEL_COMPACT_MAX_CHARS:
        label = format_dev_cycle_compact(n, cap)
        if outer_label:
            initial = outer_label[:1]
            rendered = f"{initial}{label[1:]}"
            return _pad_to_cells(_tail_truncate(rendered, max_chars), max_chars)
        return _pad_to_cells(label, max_chars)
    minimal = format_dev_cycle_minimal(n, cap)
    return _pad_to_cells(_tail_truncate(minimal, max_chars), max_chars)


def _outer_label_canonical_chars(outer_label: str | None) -> int:
    suffix_max = _OUTER_DEV_LABEL_SUFFIX_MAX_CHARS
    if outer_label is None:
        return _OUTER_DEV_LABEL_MAX_CHARS
    return cell_len(_safe_single_line(outer_label)) + 1 + suffix_max


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"Time {hours}:{minutes:02d}:{secs:02d}"
    return f"Time {minutes:02d}:{secs:02d}"


_ELAPSED_FIXED_WIDTH: int = 13
assert len("Time 99:59:59") == _ELAPSED_FIXED_WIDTH, (
    f"_ELAPSED_FIXED_WIDTH must match the worst-case realistic label width; "
    f"got {_ELAPSED_FIXED_WIDTH} but 'Time 99:59:59' is {len('Time 99:59:59')} chars"
)


def _format_elapsed_fixed(seconds: float | None) -> str:
    if seconds is None:
        return " " * _ELAPSED_FIXED_WIDTH
    return _format_elapsed(seconds).ljust(_ELAPSED_FIXED_WIDTH)


def _resolve_elapsed_seconds(model: StatusBarModel, now_monotonic: float | None) -> float | None:
    if (
        model.run_started_monotonic is not None
        and now_monotonic is not None
        and now_monotonic >= model.run_started_monotonic
    ):
        return now_monotonic - model.run_started_monotonic
    return model.elapsed_seconds


def _liveness_frame(ctx: DisplayContext, now_monotonic: float | None) -> str:
    if now_monotonic is None:
        return ctx.glyph_for("liveness")
    frames = ("⠋", "⠙", "⠹", "⠸") if ctx.glyphs_enabled else ("-", "\\", "|", "/")
    return frames[int(now_monotonic) % len(frames)]


def _format_elapsed_short(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return " " * _ELAPSED_SHORT_FIXED_WIDTH
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m".ljust(_ELAPSED_SHORT_FIXED_WIDTH)
    return f"{minutes}m{secs:02d}s".ljust(_ELAPSED_SHORT_FIXED_WIDTH)


def _split_agent_label(label: str) -> tuple[str, str]:
    prefix = "Agent "
    if label.startswith(prefix):
        return prefix, label[len(prefix) :]
    return "", label


def _resolve_elapsed_label(model: StatusBarModel, now_monotonic: float | None) -> str:
    base = _format_elapsed(model.elapsed_seconds) if model.elapsed_seconds is not None else ""
    if (
        model.run_started_monotonic is not None
        and now_monotonic is not None
        and now_monotonic >= model.run_started_monotonic
    ):
        return _format_elapsed(now_monotonic - model.run_started_monotonic)
    return base


def _prune_optional_segments(
    candidates: tuple[str, ...], separator: str, path_budget: int
) -> list[str]:
    keep: list[str] = [label for label in candidates if label]
    keep_width = sum(cell_len(separator) + cell_len(label) for label in keep)
    while keep and keep_width > path_budget - _MIN_PATH_BUDGET:
        keep.pop()
        keep_width = sum(cell_len(separator) + cell_len(label) for label in keep)
    return keep


_ATTENTION_ALIASES: dict[str, str] = {
    "intervention": "intervention",
    "intervention_required": "intervention_required",
    "report_intervention_required": "intervention_required",
    "ladder_escalation": "evidence_diagnosis",
    "conflict": "conflict_resolution",
    "rebase_conflict": "conflict_resolution",
    "diagnosis": "evidence_diagnosis",
    "evidence": "evidence_diagnosis",
    "resolve_active_operation": "conflict_resolution",
    "continue_git_operation": "integration",
    "reintegrate_against_target": "integration",
    "land_target": "landing",
    "publish_after_landing": "publication",
    "wait_for_bounded_retry": "waiting",
    "require_intervention": "intervention",
}


def _resolve_attention_state(model: StatusBarModel) -> str | None:
    pushed = model.attention if model.attention is not None else model.operation_display_state
    if pushed is None:
        return None
    if pushed in ATTENTION_PRESENTATION:
        return pushed
    normalized = pushed.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in ATTENTION_PRESENTATION:
        return normalized
    return _ATTENTION_ALIASES.get(normalized)


def _format_analysis_label(n: int, cap: int | None, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if max_chars >= _INNER_ANALYSIS_LABEL_MAX_CHARS:
        return format_analysis_cycle(n, cap).ljust(max_chars)
    if max_chars >= _INNER_ANALYSIS_LABEL_COMPACT_MAX_CHARS:
        return format_analysis_cycle_compact(n, cap).ljust(max_chars)
    return format_analysis_cycle_minimal(n, cap).ljust(max_chars)


def _append_attention_slot(
    text: Text,
    model: StatusBarModel,
    ctx: DisplayContext,
    separator: str,
) -> None:
    attention_state = _resolve_attention_state(model)
    attention_slot_width = _attention_slot_reserved_width(ctx)
    if attention_state is not None:
        label, glyph_key, style = ATTENTION_PRESENTATION[attention_state]
        glyph = ctx.glyph_for(glyph_key)
        status_name = {
            "theme.status.error": "error",
            "theme.status.warn": "warning",
            "theme.status.info": "info",
        }[style]
        text.append(
            f"{glyph} {label}",
            style=_fresh_style(
                pick_status_styles(
                    ctx.terminal_background_is_light,
                    surface_hex=ctx.terminal_background_hex,
                )[status_name][0]
            ),
        )
        text.append(separator, style=ctx.theme.styles["theme.status.path_marker"])

        rendered_so_far = cell_len(text.plain)
        if rendered_so_far < attention_slot_width:
            text.append(
                " " * (attention_slot_width - rendered_so_far),
                style="theme.status.path",
            )
    else:
        text.append(
            " " * attention_slot_width,
            style="theme.status.path",
        )


def _append_integration_alert(
    text: Text,
    alert: str,
    ctx: DisplayContext,
    separator: str,
) -> None:
    alert_display = _safe_single_line(alert)
    alert_lower = alert_display.lower()
    is_diag = "evidence" in alert_lower or "diagnosis" in alert_lower
    alert_style = "theme.status.info" if is_diag else "theme.status.error"
    alert_glyph = ctx.glyph_for("info") if is_diag else ctx.glyph_for("warning")
    text.append(f"{alert_glyph} {alert_display}", style=alert_style)
    text.append(separator, style="theme.status.path_marker")


def _finalize_status_bar_width(
    text: Text,
    model: StatusBarModel,
    ctx: DisplayContext,
) -> Text:
    if ctx.width < 1:
        return Text(" ")
    if cell_len(text.plain) <= ctx.width:
        return text
    if (
        model.integration_alert
        or model.integration_status is not None
        or "check-config" in text.plain
        or "fix " in text.plain
    ):
        lines = text.wrap(ctx.console, ctx.width)
        return Text("\n").join(lines)
    text.truncate(ctx.width)
    return text


def render_status_bar(
    model: StatusBarModel,
    ctx: DisplayContext,
    *,
    home: str | None = None,
    now_monotonic: float | None = None,
) -> Text:
    separator = _field_separator(ctx)
    path_display = _safe_single_line(_home_relative(model.workspace_root, home))
    phase_display = _safe_single_line(model.phase_label)

    has_outer_dev = model.outer_dev_iteration is not None
    has_inner_analysis = model.inner_analysis is not None
    outer_label = _safe_single_line(model.outer_label) if model.outer_label else None
    integration_alert = (
        model.integration_status.summary
        if model.integration_status is not None
        else model.integration_alert
    )
    alert_len = 0
    if integration_alert:
        alert_len = cell_len(_safe_single_line(integration_alert)) + cell_len(separator) + 2
    budgets = _field_overhead_and_label_budgets(
        ctx,
        has_outer_dev=has_outer_dev,
        has_inner_analysis=has_inner_analysis,
        has_agent=model.agent_name is not None,
        outer_label_canonical_chars=_outer_label_canonical_chars(outer_label),
        integration_alert_len=alert_len,
    )

    agent_label = f"Agent {_safe_single_line(model.agent_name)}" if model.agent_name else ""
    optional_segments = [agent_label] if agent_label and ctx.width >= _AGENT_FIT_THRESHOLD else []
    if ctx.width < _PHASE_ABBREVIATE_THRESHOLD:
        phase_display = _abbreviated_phase_label(phase_display, budgets.phase_budget, ctx.width)
    else:
        phase_display = _tail_truncate(phase_display, budgets.phase_budget)
    render_outer_dev = has_outer_dev and budgets.outer_dev_label_max_chars > 0
    render_inner_analysis = has_inner_analysis and budgets.inner_analysis_label_max_chars > 0
    text = Text()
    _append_attention_slot(text, model, ctx, separator)
    if integration_alert:
        _append_integration_alert(text, integration_alert, ctx, separator)
    if ctx.glyphs_enabled and budgets.render_marker:
        marker = ctx.glyph_for("phase_marker")
        text.append(marker + " ", style="theme.status.bar_marker")
    text.append(phase_display, style=model.phase_style)
    if ctx.width >= _FLOOR_THRESHOLD:
        text.append(separator, style="theme.status.path_marker")
        liveness_glyph = _liveness_frame(ctx, now_monotonic)
        text.append(liveness_glyph, style="theme.status.info")
        if ctx.width >= _CANONICAL_FIT_THRESHOLD:
            text.append(
                _format_elapsed_fixed(_resolve_elapsed_seconds(model, now_monotonic)),
                style="theme.status.info",
            )
        else:
            text.append(
                _format_elapsed_short(_resolve_elapsed_seconds(model, now_monotonic)),
                style="theme.status.info",
            )
    if render_outer_dev:
        text.append(separator, style="theme.status.path_marker")
        if budgets.render_iter_glyph:
            text.append(ctx.glyph_for("outer_dev") + " ", style="theme.outer_dev")
        text.append(
            _format_dev_label(
                model.outer_dev_iteration or 0,
                model.outer_dev_cap,
                budgets.outer_dev_label_max_chars,
                outer_label=outer_label,
            ),
            style="theme.outer_dev",
        )
    if render_inner_analysis:
        text.append(separator, style="theme.status.path_marker")
        if budgets.render_iter_glyph:
            text.append(ctx.glyph_for("inner_analysis") + " ", style="theme.inner_analysis")
        text.append(
            _format_analysis_label(
                model.inner_analysis or 0,
                model.inner_analysis_cap,
                budgets.inner_analysis_label_max_chars,
            ),
            style="theme.inner_analysis",
        )
    for label in optional_segments:
        _append_optional_segment(text, label, model, separator, ctx)
    path_room = ctx.width - cell_len(text.plain) - cell_len(separator)
    last_segment = path_display.rsplit(os.sep, 1)[-1]
    last_segment_width = cell_len(last_segment)
    path_display = (
        _middle_truncate_path(path_display, path_room)
        if path_room >= last_segment_width + cell_len(_ELLIPSIS) + 1
        else last_segment
        if path_room >= last_segment_width
        else ""
    )
    _append_path_segment(text, path_display, separator, ctx.width)
    return _finalize_status_bar_width(text, model, ctx)


def _append_optional_segment(
    text: Text,
    label: str,
    model: StatusBarModel,
    separator: str,
    ctx: DisplayContext,
) -> None:
    text.append(separator, style="theme.status.path_marker")
    agent_width = (
        _AGENT_LABEL_FIXED_WIDTH
        if ctx.width < _CANONICAL_FIT_THRESHOLD
        else cell_len("Agent pi · minimax/MiniMax-3")
    )
    label = _pad_to_cells(_tail_truncate(label, agent_width), agent_width)
    if label.startswith("Agent ") and model.agent_name:
        agent_prefix, agent_rest = _split_agent_label(label)
        if agent_prefix:
            text.append(agent_prefix, style="theme.status.info")
        if agent_rest:
            text.append(
                agent_rest,
                style=identity_color(
                    model.agent_name,
                    active=(*_DISPLAY_IDENTITY_ACTIVE_SET, model.agent_name),
                    terminal_bg_is_light=ctx.terminal_background_is_light,
                ),
            )
    else:
        text.append(label, style="theme.status.info")


def _append_path_segment(
    text: Text,
    path_display: str,
    separator: str,
    width: int,
) -> None:
    if width > _PATH_DROP_THRESHOLD and path_display:
        text.append(separator, style="theme.status.path_marker")
        text.append(path_display, style="theme.status.path")


class StatusBar:
    __slots__ = (
        "_clock",
        "_display",
        "_durable_frame",
        "_fallback_frame",
        "_fallback_rendered",
        "_home",
        "_last_durable_state_key",
        "_last_fallback_state_key",
        "_last_live_frame",
        "_live",
        "_live_frame_rendered",
        "_lock",
        "_model",
        "_started",
        "_ticker",
        "_ticker_stop",
    )

    def __init__(
        self,
        display: ParallelDisplay,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._display: _StatusBarHost = display
        self._home = str(pathlib.Path.home())
        self._model: StatusBarModel | None = None
        self._live: _Live | None = None
        self._lock = threading.RLock()
        self._fallback_rendered = False
        self._fallback_frame: str | None = None
        self._started = False
        self._durable_frame: str | None = None
        self._last_durable_state_key: tuple[object, ...] | None = None
        self._last_fallback_state_key: tuple[object, ...] | None = None
        self._last_live_frame: str | None = None
        self._live_frame_rendered = False
        self._ticker: threading.Thread | None = None
        self._ticker_stop: threading.Event | None = None
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic

    @property
    def model(self) -> StatusBarModel | None:
        return self._model

    @property
    def is_active(self) -> bool:
        return self._live is not None

    @property
    def last_model(self) -> StatusBarModel | None:
        return self._model

    def _ctx(self) -> DisplayContext:
        return self._display._ctx

    def _real_tty(self) -> bool:
        ctx = self._ctx()
        console = ctx.console
        is_terminal_attr: bool | None = getattr(console, "is_terminal", None)
        if not is_terminal_attr:
            return False
        file_obj: IO[str] = console.file
        isatty_result: bool = file_obj.isatty()
        return is_terminal_attr and isatty_result

    def _gate(self) -> bool:
        if self._live is not None:
            return False
        if bool(self._display._is_quiet):
            return False
        return self._real_tty()

    def _renderable(self) -> Text:
        model = self._model
        if model is None:
            return Text(" ")
        model = self._model_with_live_attention(model)
        if model.run_started_monotonic is None:
            return render_status_bar(model, self._ctx(), home=self._home)
        return render_status_bar(
            model,
            self._ctx(),
            home=self._home,
            now_monotonic=self._clock(),
        )

    def _model_with_live_attention(self, model: StatusBarModel) -> StatusBarModel:
        raw_attention: object = getattr(self._display, "watchdog_attention", None)
        if raw_attention is None:
            return model
        if model.attention is not None:
            return model
        if raw_attention not in ATTENTION_PRESENTATION:
            return model
        return dataclasses.replace(model, attention=raw_attention)

    def _live_console_is_interactive(self) -> bool:
        is_interactive: object = getattr(self._ctx().console, "is_interactive", False)
        return is_interactive is True

    def _fallback_render_once(self) -> None:
        if self._live_console_is_interactive():
            return
        model = self._model
        if model is not None and model.attention is not None:
            state_key = (
                model.attention,
                model.operation_display_state,
                model.integration_alert,
                model.integration_status,
                model.phase_label,
                model.outer_dev_iteration,
                model.agent_name,
            )
            if state_key == self._last_fallback_state_key:
                return
            self._last_fallback_state_key = state_key
        renderable = self._renderable()
        if self._fallback_rendered and self._fallback_frame == renderable.plain:
            return
        self._fallback_cleanup()
        self._ctx().console.print(renderable)
        self._fallback_rendered = True
        self._fallback_frame = renderable.plain

    def _emit_durable_transition_if_changed(self) -> None:
        if (
            self._real_tty()
            or (self._started and self._ctx().console.is_terminal)
            or bool(self._display._is_quiet)
        ):
            return
        model = self._model
        if model is None or model.attention is None:
            return
        state_key = (
            model.attention,
            model.operation_display_state,
            model.integration_alert,
            model.integration_status,
            model.phase_label,
            model.outer_dev_iteration,
            model.agent_name,
        )
        if state_key == self._last_durable_state_key:
            return
        renderable = self._renderable()
        if renderable.plain == self._durable_frame:
            return
        self._ctx().console.print(renderable)
        self._durable_frame = renderable.plain
        self._last_durable_state_key = state_key

    def _fallback_cleanup(self) -> None:
        if not self._fallback_rendered:
            return
        self._fallback_rendered = False
        self._fallback_frame = None
        if not self._real_tty() or not terminal_understands_vt():
            return
        return

    def _refresh_live_if_changed(self) -> bool:
        with self._lock:
            live = self._live
            if live is None:
                return False
            frame = self._renderable().plain
            if frame == self._last_live_frame:
                return False
            self._last_live_frame = frame
            with contextlib.suppress(Exception):
                live.refresh()
                self._live_frame_rendered = True
                return True
        return False

    def _run_ticker(self, stop: threading.Event) -> None:
        while not stop.wait(1 / _STATUS_BAR_REFRESH_PER_SECOND):
            self._refresh_live_if_changed()

    def _start_ticker(self) -> None:
        stop = threading.Event()
        ticker = threading.Thread(
            target=self._run_ticker,
            args=(stop,),
            name="ralph-status-bar",
            daemon=True,
        )
        self._ticker_stop = stop
        self._ticker = ticker
        ticker.start()

    def _stop_ticker(self) -> None:
        stop = self._ticker_stop
        ticker = self._ticker
        self._ticker_stop = None
        self._ticker = None
        if stop is not None:
            stop.set()
        if ticker is not None and ticker is not threading.current_thread():
            ticker.join(timeout=1.0)

    def start(self) -> None:
        self._started = True
        if not self._gate():
            return
        with contextlib.suppress(Exception):
            from rich.live import Live

            renderable = self._renderable()
            live = Live(
                renderable,
                console=self._ctx().console,
                transient=_STATUS_BAR_TRANSIENT,
                auto_refresh=False,
                screen=False,
                get_renderable=self._renderable,
            )
            live.start()
            self._live = live
            self._last_live_frame = renderable.plain
            with contextlib.suppress(Exception):
                live.refresh()
                self._live_frame_rendered = True
            self._start_ticker()
            self._fallback_render_once()

    def stop(self) -> None:
        live = self._live
        if live is None:
            return
        self._live = None
        self._last_live_frame = None
        self._stop_ticker()
        with contextlib.suppress(Exception):
            live.stop()
        self._live_frame_rendered = False
        with contextlib.suppress(Exception):
            self._fallback_cleanup()

    def update(self, model: StatusBarModel) -> None:
        with self._lock:
            self._model = model
        if self._live is not None:
            with contextlib.suppress(Exception):
                self._fallback_render_once()
            return
        self._emit_durable_transition_if_changed()
