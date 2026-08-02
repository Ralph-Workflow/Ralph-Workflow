"""Structural entitlement and format metadata for user-visible display surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class SurfaceSpec:
    """One user-visible display surface and its production entry points."""

    name: str
    owner: str
    frame_entitled: bool = False
    format: str = ""
    overflow_policy: str = "fold: preserve carriers and recovery-relevant tails"
    scene: str = "clean_run"
    entry_points: tuple[str, ...] = ()


SURFACE_CATALOG: Final[tuple[SurfaceSpec, ...]] = (
    SurfaceSpec(
        "welcome",
        "parallel_display",
        True,
        "frame: operator welcome",
        scene="first_screen",
        entry_points=("emit_welcome_banner",),
    ),
    SurfaceSpec(
        "first_run",
        "parallel_display",
        True,
        "frame: first-run guidance",
        scene="first_screen",
        entry_points=("emit_first_run_panel",),
    ),
    SurfaceSpec(
        "run_open",
        "parallel_display",
        True,
        "frame: outcome-first run identity",
        scene="first_screen",
        entry_points=("emit_run_start",),
    ),
    SurfaceSpec(
        "phase_open",
        "parallel_display",
        format="rule: phase, state, duration",
        entry_points=("emit_phase_start", "emit_phase_start_from_entry"),
    ),
    SurfaceSpec(
        "phase_close",
        "parallel_display",
        format="rule: phase, outcome, duration",
        entry_points=("emit_phase_close", "emit_phase_close_from_exit", "emit_phase_close_banner"),
    ),
    SurfaceSpec(
        "phase_transition",
        "parallel_display",
        format="rule: previous outcome, next phase",
        entry_points=("emit_phase_transition",),
    ),
    SurfaceSpec(
        "agent_text",
        "agent_event_renderer",
        format="grid: timestamp | category | unit | body",
        entry_points=("emit_activity_line", "emit_log_line", "emit_parsed_event"),
    ),
    SurfaceSpec(
        "reasoning",
        "agent_event_renderer",
        format="grid: timestamp | category | unit | body",
        entry_points=("emit_analysis_result",),
    ),
    SurfaceSpec(
        "tool_call",
        "agent_event_renderer",
        format="grid: timestamp | category | unit | body",
        scene="burst",
    ),
    SurfaceSpec(
        "tool_result",
        "agent_event_renderer",
        format="grid: timestamp | category | unit | body",
        scene="burst",
    ),
    SurfaceSpec(
        "tool_error",
        "agent_event_renderer",
        format="grid: timestamp | error | unit | cause",
        scene="failure",
    ),
    SurfaceSpec(
        "raw_warning_status",
        "parallel_display",
        format="grid: timestamp | state | unit | body",
        scene="failure",
        entry_points=("emit_status_line", "emit_warn_line"),
    ),
    SurfaceSpec(
        "table",
        "parallel_display",
        format="table: aligned labels and values",
        scene="clean_run",
        entry_points=(
            "emit_agents_table",
            "emit_providers_table",
            "emit_config_table",
            "emit_metrics_table",
            "emit_checkpoint_summary_table",
            "emit_diagnose_inventory_table",
            "emit_diagnose_probe_table",
            "emit_diagnose_servers_table",
        ),
    ),
    SurfaceSpec(
        "cli_status",
        "parallel_display",
        format="label: INFO state message",
        scene="clean_run",
        entry_points=("emit_status",),
    ),
    SurfaceSpec(
        "cli_warning",
        "parallel_display",
        format="label: WARN recovery message",
        scene="failure",
        entry_points=("emit_warning", "emit_skill_failure_warning", "emit_fallback_next_steps"),
    ),
    SurfaceSpec(
        "panel",
        "parallel_display",
        format="indent: titled content when populated",
        scene="clean_run",
        entry_points=("emit_info_panel", "emit_renderable"),
    ),
    SurfaceSpec(
        "artifact",
        "parallel_display",
        format="indent: label, path, recovery",
        scene="clean_run",
        entry_points=(
            "emit_plan_artifact",
            "emit_development_artifact",
            "emit_review_artifact",
            "emit_fix_artifact",
            "emit_analysis_decision",
            "emit_commit_message",
            "emit_missing_plan_hint",
        ),
    ),
    SurfaceSpec(
        "syntax_preview",
        "edit_preview",
        format="indent: shared unit; numbered source rows",
    ),
    SurfaceSpec(
        "diff_preview",
        "edit_preview",
        format="indent: shared unit; numbered polarity rows",
    ),
    SurfaceSpec(
        "elision",
        "content_condenser",
        format="marker: count, bytes, recovery destination",
        overflow_policy="elide: count, size, and recoverable transcript destination",
        scene="burst",
    ),
    SurfaceSpec(
        "status_bar",
        "status_bar",
        format="single row: state, phase, elapsed",
        scene="idle_stretch",
    ),
    SurfaceSpec(
        "completion_success",
        "completion_summary",
        True,
        "frame: outcome, metrics, recovery",
        scene="closing_screen",
        entry_points=("emit_completion_summary_panel", "emit_run_end"),
    ),
    SurfaceSpec(
        "completion_failure",
        "completion_summary",
        True,
        "frame: cause, metrics, recovery",
        scene="failure",
    ),
    SurfaceSpec(
        "capability",
        "parallel_display",
        format="list: capability name and state",
        scene="first_screen",
        entry_points=("emit_capability_summary",),
    ),
    SurfaceSpec(
        "dry_run",
        "parallel_display",
        format="recap: planned work and recovery",
        scene="closing_screen",
        entry_points=("emit_dry_run_summary",),
    ),
    SurfaceSpec(
        "blank_gap",
        "parallel_display",
        format="gap: deliberate structural separation",
        entry_points=("emit_blank_line",),
    ),
    SurfaceSpec(
        "snapshot",
        "parallel_display",
        format="grid: phase, state, recovery",
        scene="failure",
        entry_points=("emit_snapshot",),
    ),
)


__all__ = ["SURFACE_CATALOG", "SurfaceSpec"]
