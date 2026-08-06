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
    production_entry_points: tuple[str, ...] = ()


SURFACE_CATALOG: Final[tuple[SurfaceSpec, ...]] = (
    SurfaceSpec(
        "welcome",
        "parallel_display",
        True,
        "frame: operator welcome",
        scene="first_screen",
        entry_points=("emit_welcome_banner",),
        production_entry_points=("ParallelDisplay.emit_welcome_banner",),
    ),
    SurfaceSpec(
        "first_run",
        "parallel_display",
        True,
        "frame: first-run guidance",
        scene="first_screen",
        entry_points=("emit_first_run_panel",),
        production_entry_points=("ParallelDisplay.emit_first_run_panel",),
    ),
    SurfaceSpec(
        "run_open",
        "parallel_display",
        True,
        "frame: outcome-first run identity",
        scene="first_screen",
        entry_points=("emit_run_start",),
        production_entry_points=("ParallelDisplay.emit_run_start",),
    ),
    SurfaceSpec(
        "phase_open",
        "parallel_display",
        format="rule: phase, state, duration",
        scene="clean_run",
        entry_points=("emit_phase_start", "emit_phase_start_from_entry"),
        production_entry_points=(
            "ParallelDisplay.emit_phase_start",
            "ParallelDisplay.emit_phase_start_from_entry",
        ),
    ),
    SurfaceSpec(
        "phase_close",
        "parallel_display",
        format="rule: phase, outcome, duration",
        scene="clean_run",
        entry_points=("emit_phase_close", "emit_phase_close_from_exit", "emit_phase_close_banner"),
        production_entry_points=(
            "ParallelDisplay.emit_phase_close",
            "ParallelDisplay.emit_phase_close_from_exit",
            "ParallelDisplay.emit_phase_close_banner",
        ),
    ),
    SurfaceSpec(
        "phase_transition",
        "parallel_display",
        format="rule: previous outcome, next phase",
        scene="clean_run",
        entry_points=("emit_phase_transition",),
        production_entry_points=("ParallelDisplay.emit_phase_transition",),
    ),
    SurfaceSpec(
        "agent_text",
        "agent_event_renderer",
        format="grid: timestamp | category | unit | body",
        scene="clean_run",
        entry_points=("emit_activity_line", "emit_log_line", "emit_parsed_event"),
        production_entry_points=(
            "ParallelDisplay.emit_activity_line",
            "ParallelDisplay.emit_log_line",
            "ParallelDisplay.emit_parsed_event",
        ),
    ),
    SurfaceSpec(
        "reasoning",
        "agent_event_renderer",
        format="grid: timestamp | category | unit | body",
        scene="clean_run",
        entry_points=("emit_analysis_result",),
        production_entry_points=("ParallelDisplay.emit_analysis_result",),
    ),
    SurfaceSpec(
        "tool_call",
        "agent_event_renderer",
        format="grid: timestamp | category | unit | body",
        scene="burst",
        production_entry_points=("ParallelDisplay.emit_activity_line",),
    ),
    SurfaceSpec(
        "tool_result",
        "agent_event_renderer",
        format="grid: timestamp | category | unit | body",
        scene="burst",
        production_entry_points=("ParallelDisplay.emit_activity_line",),
    ),
    SurfaceSpec(
        "tool_error",
        "agent_event_renderer",
        format="grid: timestamp | error | unit | cause",
        scene="failure",
        production_entry_points=("ParallelDisplay.emit_activity_line",),
    ),
    SurfaceSpec(
        "raw_warning_status",
        "parallel_display",
        format="grid: timestamp | state | unit | body",
        scene="clean_run",
        entry_points=("emit_status_line", "emit_warn_line"),
        production_entry_points=(
            "ParallelDisplay.emit_status_line",
            "ParallelDisplay.emit_warn_line",
        ),
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
        production_entry_points=(
            "ParallelDisplay.emit_agents_table",
            "ParallelDisplay.emit_providers_table",
            "ParallelDisplay.emit_config_table",
            "ParallelDisplay.emit_metrics_table",
            "ParallelDisplay.emit_checkpoint_summary_table",
            "ParallelDisplay.emit_diagnose_inventory_table",
            "ParallelDisplay.emit_diagnose_probe_table",
            "ParallelDisplay.emit_diagnose_servers_table",
        ),
    ),
    SurfaceSpec(
        "cli_status",
        "parallel_display",
        format="label: INFO state message",
        scene="clean_run",
        entry_points=("emit_status",),
        production_entry_points=("ParallelDisplay.emit_status",),
    ),
    SurfaceSpec(
        "cli_warning",
        "parallel_display",
        format="label: WARN recovery message",
        scene="clean_run",
        entry_points=("emit_warning", "emit_skill_failure_warning", "emit_fallback_next_steps"),
        production_entry_points=(
            "ParallelDisplay.emit_warning",
            "ParallelDisplay.emit_skill_failure_warning",
            "ParallelDisplay.emit_fallback_next_steps",
        ),
    ),
    SurfaceSpec(
        "panel",
        "parallel_display",
        format="indent: titled content when populated",
        scene="clean_run",
        entry_points=("emit_info_panel", "emit_renderable"),
        production_entry_points=(
            "ParallelDisplay.emit_info_panel",
            "ParallelDisplay.emit_renderable",
        ),
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
        production_entry_points=(
            "ParallelDisplay.emit_plan_artifact",
            "ParallelDisplay.emit_development_artifact",
            "ParallelDisplay.emit_review_artifact",
            "ParallelDisplay.emit_fix_artifact",
            "ParallelDisplay.emit_analysis_decision",
            "ParallelDisplay.emit_commit_message",
            "ParallelDisplay.emit_missing_plan_hint",
        ),
    ),
    SurfaceSpec(
        "syntax_preview",
        "edit_preview",
        format="indent: shared unit; numbered source rows",
        production_entry_points=("build_edit_preview",),
    ),
    SurfaceSpec(
        "file_preview",
        "edit_preview",
        format="indent: shared unit; read-write body, no polarity rows",
        production_entry_points=("build_edit_preview",),
    ),
    SurfaceSpec(
        "diff_preview",
        "edit_preview",
        format="indent: shared unit; numbered polarity rows",
        production_entry_points=("build_edit_preview",),
    ),
    SurfaceSpec(
        "elision",
        "content_condenser",
        format="marker: count, bytes, recovery destination",
        overflow_policy="elide: count, size, and recoverable transcript destination",
        scene="burst",
        production_entry_points=("condense_content",),
    ),
    SurfaceSpec(
        "status_bar",
        "status_bar",
        format="single row: state, phase, elapsed",
        scene="idle_stretch",
        production_entry_points=("ParallelDisplay.update_status_bar",),
    ),
    SurfaceSpec(
        "completion_success",
        "completion_summary",
        True,
        "frame: outcome, metrics, recovery",
        scene="closing_screen",
        entry_points=("emit_completion_summary_panel", "emit_run_end"),
        production_entry_points=(
            "ParallelDisplay.emit_completion_summary_panel",
            "ParallelDisplay.emit_run_end",
        ),
    ),
    SurfaceSpec(
        "completion_failure",
        "completion_summary",
        True,
        "frame: cause, metrics, recovery",
        scene="failure",
        entry_points=("emit_completion_summary_panel",),
        production_entry_points=("ParallelDisplay.emit_completion_summary_panel",),
    ),
    SurfaceSpec(
        "capability",
        "parallel_display",
        format="list: capability name and state",
        scene="first_screen",
        entry_points=("emit_capability_summary",),
        production_entry_points=("ParallelDisplay.emit_capability_summary",),
    ),
    SurfaceSpec(
        "dry_run",
        "parallel_display",
        format="recap: planned work and recovery",
        scene="closing_screen",
        entry_points=("emit_dry_run_summary",),
        production_entry_points=("ParallelDisplay.emit_dry_run_summary",),
    ),
    SurfaceSpec(
        "blank_gap",
        "parallel_display",
        format="gap: deliberate structural separation",
        scene="clean_run",
        entry_points=("emit_blank_line",),
        production_entry_points=("ParallelDisplay.emit_blank_line",),
    ),
    SurfaceSpec(
        "snapshot",
        "parallel_display",
        format="grid: phase, state, recovery",
        scene="failure",
        entry_points=("emit_snapshot",),
        production_entry_points=("ParallelDisplay.emit_snapshot",),
    ),
)


__all__ = ["SURFACE_CATALOG", "SurfaceSpec"]
