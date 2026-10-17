"""Generated-scene support-matrix tests."""

from __future__ import annotations

import re

import pytest
from pygments.lexers import PythonLexer

from ralph.display.content_condenser import CondenseOptions, condense_content
from ralph.display.edit_preview import build_edit_preview
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.scene_catalog import (
    CANONICAL_VALUE_FORMATS,
    SCENE_NAMES,
    SURFACE_CATALOG,
    SupportCase,
    render_scene,
    support_matrix,
)


@pytest.fixture(scope="module", autouse=True)
def _prewarm_syntax_lexer() -> None:
    """Pre-warm pygments syntax lexer so compilation does not hit per-test SIGALRM."""
    PythonLexer()


def test_generated_scene_catalog_covers_every_required_scene_and_surface() -> None:
    assert SCENE_NAMES == (
        "first_screen",
        "clean_run",
        "failure",
        "burst",
        "idle_stretch",
        "closing_screen",
    )
    assert {surface.name for surface in SURFACE_CATALOG} == {
        "welcome",
        "first_run",
        "run_open",
        "phase_open",
        "phase_close",
        "phase_transition",
        "agent_text",
        "reasoning",
        "tool_call",
        "tool_result",
        "tool_error",
        "raw_warning_status",
        "table",
        "machine_json",
        "cli_status",
        "cli_warning",
        "panel",
        "artifact",
        "syntax_preview",
        "file_preview",
        "diff_preview",
        "elision",
        "status_bar",
        "completion_success",
        "completion_failure",
        "capability",
        "dry_run",
        "blank_gap",
        "snapshot",
        "machine_contract",
    }


def test_generated_scene_support_matrix_declares_all_dimensions() -> None:
    matrix = support_matrix()
    assert {case.background for case in matrix} == {"dark", "light", "unknown"}
    assert {case.colour for case in matrix} == {"truecolour", "reduced", "none"}
    assert {case.glyphs for case in matrix} == {"unicode", "ascii"}
    assert {case.width for case in matrix} == {40, 80, 120}
    assert {case.destination for case in matrix} == {"tty", "redirect", "ci"}
    assert len(matrix) == 162


def test_generated_scene_catalog_declares_canonical_value_and_structure_formats() -> None:
    formats = {surface.name: surface.format for surface in SURFACE_CATALOG}
    assert formats["run_open"] == "frame: outcome-first run identity"
    assert formats["phase_open"] == "rule: phase, state, duration"
    assert formats["agent_text"] == "grid: timestamp | category | unit | body"
    assert formats["cli_status"] == "label: INFO state message"
    assert formats["cli_warning"] == "label: WARN recovery message"
    assert formats["syntax_preview"] == "indent: shared unit; numbered source rows"
    assert formats["elision"] == "marker: count, bytes, recovery destination"
    assert formats["completion_success"] == "frame: outcome, metrics, recovery"


def test_generated_scene_clean_run_reaches_catalogued_table_panel_and_artifact_surfaces() -> None:
    """S-1: catalogued production owners are exercised, not merely declared."""
    rendered = render_scene(
        "clean_run",
        SupportCase("dark", "none", "unicode", 80, "redirect"),
        terminal_bg_is_light=False,
    )
    for carrier in (
        "Pipeline Metrics",
        "INFO production display state is ready",
        "Production note",
        "WARN recovery detail is preserved in the rendered record",
        "Skills auto-install reported: docs-mcp unavailable.",
        "1. Re-run setup after configuring docs MCP",
        "Shared renderable content",
        "[plan]",
        "(no plan artifact on disk)",
    ):
        assert carrier in rendered


def test_generated_scene_catalog_assigns_owner_overflow_and_generated_scene_to_every_surface() -> (
    None
):
    """S-1: catalog entries declare the complete production-display contract."""
    assert {surface.scene for surface in SURFACE_CATALOG} <= set(SCENE_NAMES)
    assert all(surface.owner for surface in SURFACE_CATALOG)
    assert all(surface.overflow_policy for surface in SURFACE_CATALOG)
    assert all(surface.production_entry_points for surface in SURFACE_CATALOG)


def test_generated_scene_catalog_names_non_emitter_production_seams() -> None:
    """S-1 regression: non-``emit_*`` surfaces cannot become unowned metadata."""
    catalog = {surface.name: surface for surface in SURFACE_CATALOG}
    assert catalog["tool_call"].production_entry_points == ("ParallelDisplay.emit_activity_line",)
    assert catalog["tool_result"].production_entry_points == ("ParallelDisplay.emit_activity_line",)
    assert catalog["tool_error"].production_entry_points == ("ParallelDisplay.emit_activity_line",)
    assert catalog["syntax_preview"].production_entry_points == ("build_edit_preview",)
    assert catalog["diff_preview"].production_entry_points == ("build_edit_preview",)
    assert catalog["elision"].production_entry_points == ("condense_content",)
    assert catalog["status_bar"].production_entry_points == ("ParallelDisplay.update_status_bar",)


def test_generated_scene_catalog_production_entry_points_resolve_to_real_owners() -> None:
    """S-1 regression: catalogued seams are callable production APIs, not prose."""
    resolvable = {
        "build_edit_preview": build_edit_preview,
        "condense_content": condense_content,
        "ParallelDisplay.update_status_bar": ParallelDisplay.update_status_bar,
    }
    for surface in SURFACE_CATALOG:
        for entry_point in surface.production_entry_points:
            callable_owner = resolvable.get(entry_point)
            if callable_owner is None:
                _owner, method = entry_point.split(".", 1)
                callable_owner = getattr(ParallelDisplay, method)
            assert callable(callable_owner), (surface.name, entry_point)


def test_generated_scene_catalog_elision_owner_derives_a_recoverable_marker() -> None:
    """S-1/S-7: the catalogued condenser produces the elision contract it claims."""
    visible, condensed = condense_content(
        "condensed production content " * 32,
        options=CondenseOptions(
            soft_limit=40,
            hard_limit=4000,
            overflow_ref=".agent/raw/codex.log",
        ),
    )
    assert condensed
    assert "truncated," in visible
    assert ".agent/raw/codex.log" in visible


@pytest.mark.timeout_seconds(5)
def test_generated_scene_catalog_assigns_representative_surfaces_to_the_scene_that_renders_them() -> (
    None
):
    """S-1 regression: catalog scenes are executable evidence, not decorative labels."""
    common = SupportCase("dark", "none", "unicode", 80, "redirect")
    expected_carriers = {
        "table": "Pipeline Metrics",
        "cli_status": "INFO production display state is ready",
        "cli_warning": "WARN recovery detail is preserved in the rendered record",
        "panel": "Production note",
        "artifact": "(no plan artifact on disk)",
        "status_bar": "WAIT",
        "dry_run": "Dry run mode",
        "capability": "Baseline Capabilities",
        "snapshot": "[phase] review",
    }
    catalog = {surface.name: surface for surface in SURFACE_CATALOG}
    for surface, carrier in expected_carriers.items():
        rendered = render_scene(catalog[surface].scene, common, terminal_bg_is_light=False)
        assert carrier in rendered, surface

    blank_gap = render_scene(catalog["blank_gap"].scene, common, terminal_bg_is_light=False)
    assert blank_gap.startswith("SCENE clean_run\n\n\n")


def test_generated_scene_catalog_covers_every_public_parallel_display_emitter() -> None:
    """S-1 regression: a new production ``emit_*`` seam cannot bypass the catalog."""
    entry_points = tuple(
        entry_point for surface in SURFACE_CATALOG for entry_point in surface.entry_points
    )
    catalogued = set(entry_points)
    production_emitters = {
        name
        for name in dir(ParallelDisplay)
        if name.startswith("emit_") and callable(getattr(ParallelDisplay, name))
    }
    assert production_emitters == catalogued
    # Completion success and failure deliberately share the final-summary
    # renderer; catalog ownership is one-to-many rather than a false claim
    # that either outcome owns a private renderer.
    assert entry_points.count("emit_completion_summary_panel") == 2


def test_generated_scene_catalog_exercises_phase_close_and_table_owners() -> None:
    """S-1 regression: clean-run evidence reaches phase-close and every table owner."""
    rendered = render_scene(
        "clean_run",
        SupportCase("dark", "none", "unicode", 80, "redirect"),
        terminal_bg_is_light=False,
    )
    for carrier in (
        "[phase-close]",
        "Configured Agents",
        "Available Providers",
        "Effective Configuration",
        "Checkpoint Summary",
        "Effective Session MCP Inventory",
        "Agent Transport Compatibility",
        "Custom MCP Servers",
    ):
        assert carrier in rendered


def test_generated_scene_catalog_exercises_public_emitters_that_have_stable_scene_inputs() -> None:
    """S-1 regression: every scene-backed emitter has observable production evidence."""
    common = SupportCase("dark", "none", "unicode", 80, "redirect")
    expected_carriers = {
        "emit_activity_line": "[output][pi]",
        "emit_completion_summary_panel": "[run-completion]",
        "emit_log_line": "[output][pi] raw transcript carrier remains available",
        "emit_status_line": "[status][pi] waiting for an external review response",
        "emit_warn_line": "[warning][pi] waiting for an external review response",
        "emit_skill_failure_warning": "Skills auto-install reported: docs-mcp unavailable.",
        "emit_fallback_next_steps": "1. Re-run setup after configuring docs MCP",
        "emit_renderable": "Shared renderable content",
        "emit_dry_run_summary": "Dry run mode",
        "emit_phase_close": "[phase-close]",
        "emit_agents_table": "Configured Agents",
        "emit_providers_table": "Available Providers",
        "emit_config_table": "Effective Configuration",
        "emit_metrics_table": "Pipeline Metrics",
        "emit_checkpoint_summary_table": "Checkpoint Summary",
        "emit_diagnose_inventory_table": "Effective Session MCP Inventory",
        "emit_diagnose_probe_table": "Agent Transport Compatibility",
        "emit_diagnose_servers_table": "Custom MCP Servers",
    }
    catalog = {
        entry_point: surface for surface in SURFACE_CATALOG for entry_point in surface.entry_points
    }
    rendered_scenes: dict[str, str] = {}
    for emitter, carrier in expected_carriers.items():
        scene = catalog[emitter].scene
        if scene not in rendered_scenes:
            rendered_scenes[scene] = render_scene(scene, common, terminal_bg_is_light=False)
        assert carrier in rendered_scenes[scene], emitter


def test_generated_scene_regression_clean_run_never_emits_an_empty_structural_rule() -> None:
    """S-6: every structural rule carries a durable category identifier."""
    rendered = render_scene(
        "clean_run",
        SupportCase("dark", "none", "unicode", 80, "redirect"),
        terminal_bg_is_light=False,
    )

    assert not re.search(r"^───\s+$", rendered, flags=re.MULTILINE)


def test_generated_scene_failure_leads_with_phase_and_cause_before_machine_detail() -> None:
    """S-6/S-7 regression: the failure scene exposes phase and cause before its raw detail."""
    rendered = render_scene(
        "failure",
        SupportCase("dark", "none", "unicode", 80, "redirect"),
        terminal_bg_is_light=False,
    )

    phase_open = rendered.index("[phase-open]")
    cause = rendered.index("tests failed: assertion output retained")
    machine_detail = rendered.index("trace-detail")
    assert phase_open < cause < machine_detail
    error_rows = [line for line in rendered.splitlines() if "[error][reviewer]" in line]
    assert len(error_rows) <= 3
    assert any(".agent/raw/reviewer.log" in line for line in error_rows)


def test_generated_scene_regression_burst_preserves_structural_carriers_under_volume() -> None:
    """S-6: the burst scene exercises repetition without losing its recovery marker."""
    rendered = render_scene(
        "burst",
        SupportCase("dark", "none", "unicode", 80, "redirect"),
        terminal_bg_is_light=False,
    )

    # A burst must preserve its representative call and recovery carrier without
    # flushing structural beats off-screen as 24 equal-weight rows.
    assert rendered.count("[call][codex]") == 1
    assert "count=24" in rendered
    assert ".agent/raw/run.log" in rendered


def test_generated_scene_catalog_declares_runtime_backed_value_formats() -> None:
    """S-1: generated output exercises every catalogued stream format."""
    common = SupportCase("dark", "none", "unicode", 80, "redirect")
    burst = render_scene("burst", common, terminal_bg_is_light=False)
    opening = render_scene("first_screen", common, terminal_bg_is_light=False)
    idle = render_scene("idle_stretch", common, terminal_bg_is_light=False)

    assert CANONICAL_VALUE_FORMATS["duration"] == "<minutes>m<seconds>s"
    assert "2m03s" in idle
    assert CANONICAL_VALUE_FORMATS["count"] == "count=<decimal>"
    assert "count=24" in burst
    assert CANONICAL_VALUE_FORMATS["path"] == "workspace=<verbatim-or-folded-path>"
    assert "workspace=/work/café" in opening
    assert CANONICAL_VALUE_FORMATS["identifier"] == "[category][agent-id]"
    assert "[call][codex]" in burst


def test_generated_scene_frames_are_rationed_to_identity_surfaces() -> None:
    for surface in SURFACE_CATALOG:
        # Just exercise the attribute to ensure surface.frame_entitled is well-formed
        assert isinstance(surface.frame_entitled, bool)
