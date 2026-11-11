from __future__ import annotations

import pytest
from pygments.lexers import PythonLexer

from ralph.display._salience import FrequencyTier
from ralph.display.scene_catalog import (
    FULL_LAYOUT_WIDTH,
    SCENE_NAMES,
    SURFACE_CATALOG,
    SupportCase,
    scene_salience_decisions,
    support_matrix,
)


@pytest.fixture(scope="module", autouse=True)
def _prewarm_syntax_lexer() -> None:
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


def test_generated_scene_alarm_decisions_are_never_demoted() -> None:
    case = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
    decisions = scene_salience_decisions("failure", case, terminal_bg_is_light=False)
    alarm_decisions = [d for d in decisions if d.tier is FrequencyTier.ALARM]
    assert alarm_decisions, "failure scene must bid at least one alarm-tier frame"
    assert all(d.lit for d in alarm_decisions)


@pytest.mark.timeout_seconds(5)
def test_generated_scene_replay_of_an_identical_event_sequence_is_byte_identical() -> None:
    case = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
    for scene_name in SCENE_NAMES:
        first_decisions = scene_salience_decisions(scene_name, case, terminal_bg_is_light=False)
        second_decisions = scene_salience_decisions(scene_name, case, terminal_bg_is_light=False)
        assert first_decisions == second_decisions, scene_name


@pytest.mark.timeout_seconds(5)
def test_generated_scene_no_role_oscillates_between_lit_and_demoted_without_a_state_change() -> (
    None
):
    case = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
    for scene_name in SCENE_NAMES:
        decisions = scene_salience_decisions(scene_name, case, terminal_bg_is_light=False)
        last_lit_by_role: dict[str, bool] = {}
        for decision in decisions:
            if decision.tier not in (FrequencyTier.EVENT, FrequencyTier.ALARM):
                continue
            previously_demoted = last_lit_by_role.get(decision.role) is False
            state_change_reasons = ("state change", "alarm: exempt")
            if previously_demoted and decision.lit:
                assert decision.reason in state_change_reasons, (
                    scene_name,
                    decision,
                )
            last_lit_by_role[decision.role] = decision.lit


@pytest.mark.timeout_seconds(5)
def test_generated_scene_salience_is_a_no_op_when_no_colour_reaches_the_console() -> None:
    case = SupportCase("dark", "none", "unicode", FULL_LAYOUT_WIDTH, "tty")
    for scene_name in SCENE_NAMES:
        decisions = scene_salience_decisions(scene_name, case, terminal_bg_is_light=False)
        assert all(d.lit for d in decisions), scene_name


def test_generated_scene_idle_stretch_alternating_steady_stretch_yields_decayed_decisions() -> None:
    case = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
    decisions = scene_salience_decisions("idle_stretch", case, terminal_bg_is_light=False)
    decayed = [d for d in decisions if d.reason == "decayed"]
    decayed_roles = {d.role for d in decayed}
    assert len(decayed) >= 2, (decayed, decisions)
    assert len(decayed_roles) >= 1, (decayed, decisions)


def test_generated_scene_idle_stretch_post_stretch_state_change_re_lights_in_same_frame() -> None:
    case = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
    decisions = scene_salience_decisions("idle_stretch", case, terminal_bg_is_light=False)
    state_change_decisions = [d for d in decisions if d.reason == "state change"]
    assert state_change_decisions, decisions
    post_stretch = state_change_decisions[-1]
    assert post_stretch.lit is True, post_stretch
    assert post_stretch.role == "warning", post_stretch


def test_generated_scene_idle_stretch_decisions_never_empty() -> None:
    case = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
    decisions = scene_salience_decisions("idle_stretch", case, terminal_bg_is_light=False)
    assert decisions, "idle_stretch must bid at least one salience frame"
    assert len({d.frame_index for d in decisions}) > 0
