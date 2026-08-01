"""Structural entitlement and format metadata for user-visible display surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class SurfaceSpec:
    """One user-visible display surface and its structural entitlement."""

    name: str
    owner: str
    frame_entitled: bool = False
    format: str = ""


SURFACE_CATALOG: Final[tuple[SurfaceSpec, ...]] = (
    SurfaceSpec("welcome", "parallel_display", True, "frame: operator welcome"),
    SurfaceSpec("first_run", "parallel_display", True, "frame: first-run guidance"),
    SurfaceSpec("run_open", "parallel_display", True, "frame: outcome-first run identity"),
    SurfaceSpec("phase_open", "parallel_display", format="rule: phase, state, duration"),
    SurfaceSpec("phase_close", "parallel_display", format="rule: phase, outcome, duration"),
    SurfaceSpec(
        "phase_transition", "parallel_display", format="rule: previous outcome, next phase"
    ),
    SurfaceSpec(
        "agent_text", "agent_event_renderer", format="grid: timestamp | category | unit | body"
    ),
    SurfaceSpec(
        "reasoning", "agent_event_renderer", format="grid: timestamp | category | unit | body"
    ),
    SurfaceSpec(
        "tool_call", "agent_event_renderer", format="grid: timestamp | category | unit | body"
    ),
    SurfaceSpec(
        "tool_result", "agent_event_renderer", format="grid: timestamp | category | unit | body"
    ),
    SurfaceSpec(
        "tool_error", "agent_event_renderer", format="grid: timestamp | error | unit | cause"
    ),
    SurfaceSpec(
        "raw_warning_status", "parallel_display", format="grid: timestamp | state | unit | body"
    ),
    SurfaceSpec("table", "parallel_display", format="table: aligned labels and values"),
    SurfaceSpec("panel", "parallel_display", format="indent: titled content when populated"),
    SurfaceSpec("artifact", "parallel_display", format="indent: label, path, recovery"),
    SurfaceSpec(
        "syntax_preview", "edit_preview", format="indent: shared unit; numbered source rows"
    ),
    SurfaceSpec(
        "diff_preview", "edit_preview", format="indent: shared unit; numbered polarity rows"
    ),
    SurfaceSpec(
        "elision", "content_condenser", format="marker: count, bytes, recovery destination"
    ),
    SurfaceSpec("status_bar", "status_bar", format="single row: state, phase, elapsed"),
    SurfaceSpec(
        "completion_success", "completion_summary", True, "frame: outcome, metrics, recovery"
    ),
    SurfaceSpec(
        "completion_failure", "completion_summary", True, "frame: cause, metrics, recovery"
    ),
)


__all__ = ["SURFACE_CATALOG", "SurfaceSpec"]
