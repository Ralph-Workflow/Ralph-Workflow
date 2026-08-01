"""Generated-scene support-matrix tests."""

from __future__ import annotations

import pytest

from ralph.display.scene_catalog import (
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
    if case.colour == "none":
        assert "\x1b[" not in rendered
    if case.destination == "redirect":
        assert "\r" not in rendered


@pytest.mark.parametrize(
    ("scene_name", "required_carriers"),
    (
        ("first_screen", ("RUN OPEN", "phase=planning", "project=/work/cafe\u0301")),
        ("clean_run", ("phase=development", "agent=pi", "PASS success")),
        ("failure", ("FAIL error", "phase=review", "cause=tests failed")),
        ("burst", ("agent=codex", "REPEATED count=3", "recovery=.agent/raw/run.log")),
        ("idle_stretch", ("WAIT pending", "elapsed=02:03", "state=waiting")),
        ("closing_screen", ("RUN COMPLETE", "outcome=success", "elapsed=02:03")),
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


def test_generated_scene_frames_are_rationed_to_identity_surfaces() -> None:
    framed = {surface.name for surface in SURFACE_CATALOG if surface.frame_entitled}
    assert framed == {
        "welcome",
        "first_run",
        "run_open",
        "completion_success",
        "completion_failure",
    }
