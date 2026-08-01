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
    overflow_policy: str = "fold: preserve carriers and recovery-relevant tails"
    scene: str = "clean_run"


SURFACE_CATALOG: Final[tuple[SurfaceSpec, ...]] = (
    SurfaceSpec("welcome", "parallel_display", True, "frame: operator welcome", scene="first_screen"),
    SurfaceSpec("first_run", "parallel_display", True, "frame: first-run guidance", scene="first_screen"),
    SurfaceSpec("run_open", "parallel_display", True, "frame: outcome-first run identity", scene="first_screen"),
    SurfaceSpec("phase_open", "parallel_display", format="rule: phase, state, duration", scene="clean_run"),
    SurfaceSpec("phase_close", "parallel_display", format="rule: phase, outcome, duration", scene="clean_run"),
    SurfaceSpec(
        "phase_transition", "parallel_display", format="rule: previous outcome, next phase", scene="clean_run"
    ),
    SurfaceSpec(
        "agent_text", "agent_event_renderer", format="grid: timestamp | category | unit | body", scene="clean_run"
    ),
    SurfaceSpec(
        "reasoning", "agent_event_renderer", format="grid: timestamp | category | unit | body", scene="clean_run"
    ),
    SurfaceSpec(
        "tool_call", "agent_event_renderer", format="grid: timestamp | category | unit | body", scene="burst"
    ),
    SurfaceSpec(
        "tool_result", "agent_event_renderer", format="grid: timestamp | category | unit | body", scene="burst"
    ),
    SurfaceSpec(
        "tool_error", "agent_event_renderer", format="grid: timestamp | error | unit | cause", scene="failure"
    ),
    SurfaceSpec(
        "raw_warning_status", "parallel_display", format="grid: timestamp | state | unit | body", scene="failure"
    ),
    SurfaceSpec("table", "parallel_display", format="table: aligned labels and values", scene="closing_screen"),
    SurfaceSpec("panel", "parallel_display", format="indent: titled content when populated", scene="first_screen"),
    SurfaceSpec("artifact", "parallel_display", format="indent: label, path, recovery", scene="clean_run"),
    SurfaceSpec(
        "syntax_preview", "edit_preview", format="indent: shared unit; numbered source rows", scene="clean_run"
    ),
    SurfaceSpec(
        "diff_preview", "edit_preview", format="indent: shared unit; numbered polarity rows", scene="clean_run"
    ),
    SurfaceSpec(
        "elision", "content_condenser", format="marker: count, bytes, recovery destination", overflow_policy="elide: count, size, and recoverable transcript destination", scene="burst"
    ),
    SurfaceSpec("status_bar", "status_bar", format="single row: state, phase, elapsed", scene="idle_stretch"),
    SurfaceSpec(
        "completion_success", "completion_summary", True, "frame: outcome, metrics, recovery", scene="closing_screen"
    ),
    SurfaceSpec(
        "completion_failure", "completion_summary", True, "frame: cause, metrics, recovery", scene="failure"
    ),
)


__all__ = ["SURFACE_CATALOG", "SurfaceSpec"]
