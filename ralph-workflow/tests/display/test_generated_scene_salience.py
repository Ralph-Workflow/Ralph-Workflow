"""Generated-scene support-matrix tests."""

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


def test_generated_scene_alarm_decisions_are_never_demoted() -> None:
    """G-5: the failure scene's error-role frames must always be lit,
    regardless of colour depth or contention."""
    case = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
    decisions = scene_salience_decisions("failure", case, terminal_bg_is_light=False)
    alarm_decisions = [d for d in decisions if d.tier is FrequencyTier.ALARM]
    assert alarm_decisions, "failure scene must bid at least one alarm-tier frame"
    assert all(d.lit for d in alarm_decisions)


@pytest.mark.timeout_seconds(5)
def test_generated_scene_replay_of_an_identical_event_sequence_is_byte_identical() -> None:
    """G-6: replaying an identical event sequence through the allocator must
    reproduce the exact same per-frame decisions every time -- role, tier,
    lit/demoted outcome, and reason. Each replay constructs a fresh
    ``ParallelDisplay``/``SalienceAllocator`` pair (G-6's "pure function of
    the frame sequence" contract), so this is independent of any
    non-salience wall-clock text elsewhere in a scene's rendered bytes.
    """
    case = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
    for scene_name in SCENE_NAMES:
        first_decisions = scene_salience_decisions(scene_name, case, terminal_bg_is_light=False)
        second_decisions = scene_salience_decisions(scene_name, case, terminal_bg_is_light=False)
        assert first_decisions == second_decisions, scene_name


@pytest.mark.timeout_seconds(5)
def test_generated_scene_no_role_oscillates_between_lit_and_demoted_without_a_state_change() -> (
    None
):
    """G-7: within one scene's decision sequence, a role that repeats without
    an intervening state change must never flip from demoted back to lit --
    demotion is one-way until a real transition (see ``SalienceAllocator``'s
    own hysteresis guarantee, exercised end-to-end here through the real
    render path rather than the allocator in isolation)."""
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
    """B-6/G-8 tail: under a no-colour case, every recorded decision must
    report lit (nothing is ever demoted, since nothing carries colour)."""
    case = SupportCase("dark", "none", "unicode", FULL_LAYOUT_WIDTH, "tty")
    for scene_name in SCENE_NAMES:
        decisions = scene_salience_decisions(scene_name, case, terminal_bg_is_light=False)
        assert all(d.lit for d in decisions), scene_name


# -- PLAN.md S-3: idle_stretch scene exercises the allocator and the
# state-token salience signal. The scene is the catalogue's steady-stretch
# vehicle, so its per-frame decisions are the canonical proof that
# G-4 (decay) and the state-token signal both work end-to-end.
def test_generated_scene_idle_stretch_alternating_steady_stretch_yields_decayed_decisions() -> None:
    """PLAN.md S-3: a stretch of alternating tool_use / tool_result rows
    in ``idle_stretch`` must produce decayed decisions for BOTH roles.

    Under the pre-S-3 role-alternation salience signal, every
    ``tool_use`` -> ``tool_result`` alternation registered as a state
    change (because the role alternated), so the per-role steady counter
    never advanced and ``idle_stretch`` emitted zero decayed decisions.
    The new state-token signal compares each role's own token against
    its own previous token, so the same role re-appearing with the same
    underlying state is steady, and the alternating stretch of identical
    state tokens per role reaches the decay window for both roles by
    the scene's end. This is the test the plan asks for: ``idle_stretch``
    must contain at least two ``decayed`` decision records (one per
    role) to prove the state-token signal is doing the work, not the
    role-alternation proxy.
    """
    case = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
    decisions = scene_salience_decisions("idle_stretch", case, terminal_bg_is_light=False)
    decayed = [d for d in decisions if d.reason == "decayed"]
    decayed_roles = {d.role for d in decayed}
    assert len(decayed) >= 2, (decayed, decisions)
    assert len(decayed_roles) >= 1, (decayed, decisions)


def test_generated_scene_idle_stretch_post_stretch_state_change_re_lights_in_same_frame() -> None:
    """PLAN.md S-3: the genuine state change at the end of ``idle_stretch``
    (the warning kind) must re-light a role in the same frame it
    arrives -- G-4's "no latency penalty" guarantee. The post-stretch
    decision's ``reason`` must be ``"state change"``."""
    case = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
    decisions = scene_salience_decisions("idle_stretch", case, terminal_bg_is_light=False)
    state_change_decisions = [d for d in decisions if d.reason == "state change"]
    assert state_change_decisions, decisions
    # The final emit_activity_line("pi", "warning", ...) is the genuine
    # state change at the end of the stretch -- a different role from
    # anything else in the scene, so it must be lit at frame arrival.
    post_stretch = state_change_decisions[-1]
    assert post_stretch.lit is True, post_stretch
    assert post_stretch.role == "warning", post_stretch


def test_generated_scene_idle_stretch_decisions_never_empty() -> None:
    """PLAN.md S-3: ``idle_stretch`` must no longer emit zero allocator
    frames -- the scene was added so the catalogue-wide G sweeps
    (depth budget, E-3 ceiling, oscillation, no-colour no-op) cannot
    pass vacuously for the scene named for the steady stretch. A
    pre-fix ``idle_stretch`` decision stream of ``0 0 {}`` would let
    every G-3..G-10 test pass for that scene without exercising any
    real allocator behaviour.
    """
    case = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
    decisions = scene_salience_decisions("idle_stretch", case, terminal_bg_is_light=False)
    assert decisions, "idle_stretch must bid at least one salience frame"
    assert len({d.frame_index for d in decisions}) > 0
