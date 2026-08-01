"""Generated-scene support-matrix tests."""

from __future__ import annotations

from ralph.display.scene_catalog import (
    CONTRAST_FLOOR,
    FULL_LAYOUT_WIDTH,
    GRACEFUL_HEIGHT_FLOOR,
    GRACEFUL_WIDTH_FLOOR,
    SCENE_NAMES,
    SURFACE_CATALOG,
    support_matrix,
)


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
        "panel",
        "artifact",
        "syntax_preview",
        "diff_preview",
        "elision",
        "status_bar",
        "completion_success",
        "completion_failure",
    }


def test_generated_scene_support_matrix_declares_all_dimensions() -> None:
    matrix = support_matrix()
    assert len(matrix) == 108
    assert {case.background for case in matrix} == {"dark", "light", "unknown"}
    assert {case.colour for case in matrix} == {"truecolour", "reduced", "none"}
    assert {case.glyphs for case in matrix} == {"unicode", "ascii"}
    assert {case.width for case in matrix} == {40, 80, 120}
    assert {case.destination for case in matrix} == {"tty", "redirect"}


def test_generated_scene_contract_pins_accessibility_and_layout_floors() -> None:
    assert CONTRAST_FLOOR == 4.5
    assert FULL_LAYOUT_WIDTH == 80
    assert GRACEFUL_WIDTH_FLOOR == 40
    assert GRACEFUL_HEIGHT_FLOOR == 12


def test_generated_scene_frames_are_rationed_to_identity_surfaces() -> None:
    framed = {surface.name for surface in SURFACE_CATALOG if surface.frame_entitled}
    assert framed == {
        "welcome",
        "first_run",
        "run_open",
        "completion_success",
        "completion_failure",
    }
