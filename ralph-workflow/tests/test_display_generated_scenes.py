"""Generated-scene support-matrix tests."""

from __future__ import annotations

import re
from io import StringIO

import pytest
from rich.cells import cell_len
from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.scene_catalog import (
    CANONICAL_VALUE_FORMATS,
    CONTRAST_FLOOR,
    FULL_LAYOUT_WIDTH,
    GRACEFUL_HEIGHT_FLOOR,
    GRACEFUL_WIDTH_FLOOR,
    SCENE_NAMES,
    SURFACE_CATALOG,
    SupportCase,
    render_scene,
    support_matrix,
)
from ralph.display.theme import preview_background_for_background


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
        "cli_status",
        "cli_warning",
        "panel",
        "artifact",
        "syntax_preview",
        "diff_preview",
        "elision",
        "status_bar",
        "completion_success",
        "completion_failure",
        "capability",
        "dry_run",
        "blank_gap",
        "snapshot",
    }


def test_generated_scene_support_matrix_declares_all_dimensions() -> None:
    matrix = support_matrix()
    assert {case.background for case in matrix} == {"dark", "light", "unknown"}
    assert {case.colour for case in matrix} == {"truecolour", "reduced", "none"}
    assert {case.glyphs for case in matrix} == {"unicode", "ascii"}
    assert {case.width for case in matrix} == {40, 80, 120}
    assert {case.destination for case in matrix} == {"tty", "redirect", "ci"}
    assert len(matrix) == 162


def test_generated_scene_context_no_color_wins_over_forced_ci_capture() -> None:
    stream = StringIO()
    context = make_display_context(
        console=Console(file=stream, force_terminal=True, color_system="truecolor"),
        env={"CI": "1", "FORCE_COLOR": "1", "NO_COLOR": "1"},
    )
    context.console.print("status")
    assert not context.color_enabled
    assert "\x1b[" not in stream.getvalue()


def test_generated_scene_renderer_requires_the_resolved_case_background() -> None:
    rendered = render_scene(
        "clean_run",
        SupportCase("dark", "none", "unicode", 80, "redirect"),
        terminal_bg_is_light=False,
    )
    assert "[output][pi]" in rendered
    assert "Pipeline Metrics" in rendered
    assert "Production note" in rendered
    assert "(no plan artifact on disk)" in rendered


@pytest.mark.parametrize(("background", "rgb"), ((False, "16;20;23"), (True, "247;249;251")))
def test_generated_scene_syntax_preview_owns_the_resolved_complete_surface(
    background: bool, rgb: str
) -> None:
    """S-4: generated scenes render every preview row on the declared owned fill."""
    rendered = render_scene(
        "clean_run",
        SupportCase("light" if background else "dark", "truecolour", "unicode", 80, "tty"),
        terminal_bg_is_light=background,
    )

    preview_fill = f"\x1b[48;2;{rgb}m"
    visible = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", rendered)

    assert preview_background_for_background(background) != "default"
    # Both source rows own the same complete surface. A partial syntax-token
    # fill would emit fewer than two row fills and leave the gutter/row band ragged.
    assert rendered.count(preview_fill) >= 2
    assert "def " in visible
    assert "return len(value)" in visible


def test_generated_scene_narrow_condensed_records_keep_a_greppable_event_carrier_on_every_row() -> (
    None
):
    """S-5: folded narrow condensed records never leave a bare recovery row."""
    rendered = render_scene(
        "burst",
        SupportCase("dark", "none", "ascii", 40, "redirect"),
        terminal_bg_is_light=False,
    )
    lines = rendered.splitlines()
    first_output = next(
        index for index, line in enumerate(lines) if "[output][codex] output" in line
    )
    preview_start = next(
        index
        for index, line in enumerate(lines[first_output:], first_output)
        if line.startswith("  1 def")
    )
    condensed_rows = lines[first_output:preview_start]

    assert len(condensed_rows) > 1
    assert all("[output][codex]" in row for row in condensed_rows)
    continuation_rows = condensed_rows[1:]
    assert continuation_rows
    assert all("[output][codex]" in row for row in continuation_rows)
    assert any("count=24" in row for row in condensed_rows)
    assert any("bytes=768" in row for row in condensed_rows)
    assert any(".agent/raw" in row for row in condensed_rows)


def test_generated_scene_contract_pins_accessibility_and_layout_floors() -> None:
    assert CONTRAST_FLOOR == 4.5
    assert FULL_LAYOUT_WIDTH == 80
    assert GRACEFUL_WIDTH_FLOOR == 40
    assert GRACEFUL_HEIGHT_FLOOR == 12


@pytest.mark.timeout_seconds(5)
@pytest.mark.parametrize("case", support_matrix())
@pytest.mark.parametrize("scene_name", SCENE_NAMES)
def test_generated_scene_renderer_exercises_each_scene_across_the_declared_matrix(
    scene_name: str,
    case: SupportCase,
) -> None:
    """S-2: generated scenes are executable, destination-safe, and nonempty."""
    rendered = render_scene(
        scene_name,
        case,
        terminal_bg_is_light={"dark": False, "light": True, "unknown": None}[case.background],
    )
    assert rendered
    assert scene_name in rendered
    visible = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", rendered)
    if case.colour == "none":
        assert "\x1b[" not in rendered
    else:
        assert "\x1b[" in rendered
    if case.width == GRACEFUL_WIDTH_FLOOR:
        assert all(cell_len(line) <= case.width for line in visible.splitlines())
    if case.destination in {"redirect", "ci"}:
        assert "\r" not in rendered
        assert "\x1b[1A" not in rendered
        assert "\x1b[2K" not in rendered


@pytest.mark.parametrize(
    ("scene_name", "required_carriers"),
    (
        ("first_screen", ("Ralph Workflow", "[run-start]", "workspace=/work/cafe\u0301")),
        ("clean_run", ("Development", "[output][pi]", "phase=development")),
        (
            "failure",
            (
                "[error][reviewer]",
                "WARN recover raw machine detail",
                "tests failed",
                "Pipeline Failed",
                "failed_phase=review",
                ".agent/raw/reviewer.log",
            ),
        ),
        ("burst", ("[call][codex]", "[result][codex]", "edit_file", "count=24")),
        ("idle_stretch", ("WAIT", "Development", "2m03s")),
        ("closing_screen", ("Pipeline Complete", "agent_calls=3", "[run-completion]")),
    ),
)
def test_generated_scene_renderer_preserves_scene_specific_cold_read_carriers(
    scene_name: str, required_carriers: tuple[str, ...]
) -> None:
    rendered = render_scene(
        scene_name,
        SupportCase("dark", "none", "unicode", 80, "redirect"),
        terminal_bg_is_light=False,
    )
    for carrier in required_carriers:
        assert carrier in rendered


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


def test_generated_scene_catalog_assigns_representative_surfaces_to_the_scene_that_renders_them() -> (
    None
):
    """S-1 regression: catalog scenes are executable evidence, not decorative labels."""
    common = SupportCase("dark", "none", "unicode", 80, "redirect")
    expected_carriers = {
        "table": "Pipeline Metrics",
        "cli_status": "INFO production display state is ready",
        "panel": "Production note",
        "artifact": "(no plan artifact on disk)",
    }
    catalog = {surface.name: surface for surface in SURFACE_CATALOG}
    for surface, carrier in expected_carriers.items():
        rendered = render_scene(
            catalog[surface].scene, common, terminal_bg_is_light=False
        )
        assert carrier in rendered, surface


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
    assert len(entry_points) == len(catalogued)


def test_generated_scene_regression_clean_run_never_emits_an_empty_structural_rule() -> None:
    """S-6: every structural rule carries a durable category identifier."""
    rendered = render_scene(
        "clean_run",
        SupportCase("dark", "none", "unicode", 80, "redirect"),
        terminal_bg_is_light=False,
    )

    assert not re.search(r"^───\s+$", rendered, flags=re.MULTILINE)


def test_generated_scene_regression_burst_preserves_structural_carriers_under_volume() -> None:
    """S-6: the burst scene exercises repetition without losing its recovery marker."""
    rendered = render_scene(
        "burst",
        SupportCase("dark", "none", "unicode", 80, "redirect"),
        terminal_bg_is_light=False,
    )

    assert rendered.count("[call][codex]") >= 24
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
    framed = {surface.name for surface in SURFACE_CATALOG if surface.frame_entitled}
    assert framed == {
        "welcome",
        "first_run",
        "run_open",
        "completion_success",
        "completion_failure",
    }
