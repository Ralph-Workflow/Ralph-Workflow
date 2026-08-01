"""Structural entitlement metadata for user-visible display surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class SurfaceSpec:
    """One user-visible display surface and its structural entitlement."""

    name: str
    owner: str
    frame_entitled: bool = False


SURFACE_CATALOG: Final[tuple[SurfaceSpec, ...]] = (
    SurfaceSpec("welcome", "parallel_display", True),
    SurfaceSpec("first_run", "parallel_display", True),
    SurfaceSpec("run_open", "parallel_display", True),
    SurfaceSpec("phase_open", "parallel_display"),
    SurfaceSpec("phase_close", "parallel_display"),
    SurfaceSpec("phase_transition", "parallel_display"),
    SurfaceSpec("agent_text", "agent_event_renderer"),
    SurfaceSpec("reasoning", "agent_event_renderer"),
    SurfaceSpec("tool_call", "agent_event_renderer"),
    SurfaceSpec("tool_result", "agent_event_renderer"),
    SurfaceSpec("tool_error", "agent_event_renderer"),
    SurfaceSpec("raw_warning_status", "parallel_display"),
    SurfaceSpec("table", "parallel_display"),
    SurfaceSpec("panel", "parallel_display"),
    SurfaceSpec("artifact", "parallel_display"),
    SurfaceSpec("syntax_preview", "edit_preview"),
    SurfaceSpec("diff_preview", "edit_preview"),
    SurfaceSpec("elision", "content_condenser"),
    SurfaceSpec("status_bar", "status_bar"),
    SurfaceSpec("completion_success", "completion_summary", True),
    SurfaceSpec("completion_failure", "completion_summary", True),
)


__all__ = ["SURFACE_CATALOG", "SurfaceSpec"]
