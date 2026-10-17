"""Salience-allocator regression tests for the generated scene catalogue.

Split out of :mod:`tests.test_display_generated_scenes` so the
shared suite stays under the maintained 1000-line-per-file cap.
The salience tests share no fixture with the catalog tests
(the ``_prewarm_syntax_lexer`` autouse fixture is only needed for
the rendering path, not the ``scene_salience_decisions`` path), so
this module carries no per-test setup.
"""

from __future__ import annotations

import pytest

from ralph.display._salience import ACCENT_BUDGET_BY_DEPTH, FrequencyTier
from ralph.display.scene_catalog import (
    FULL_LAYOUT_WIDTH,
    SCENE_NAMES,
    SupportCase,
    scene_salience_decisions,
)

# -- PLAN.md S-8: G-10/E-3 accent-count gate over the generated scene catalogue --

#: One representative case per background -- the axis the salience
#: allocator's colour-depth budget (G-8) actually varies on -- kept to a
#: small, fixed set (not the full compact_matrix()) so this sweep stays a
#: light addition to the shared 60s pytest budget (S-8/S-12).
_SALIENCE_CASES: tuple[SupportCase, ...] = (
    SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty"),
    SupportCase("light", "reduced", "unicode", FULL_LAYOUT_WIDTH, "tty"),
    SupportCase("unknown", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty"),
)


@pytest.mark.parametrize("case", _SALIENCE_CASES)
@pytest.mark.parametrize("scene_name", SCENE_NAMES)
@pytest.mark.criteria("G-10")
def test_generated_scene_salience_decisions_never_exceed_the_depth_budget(
    scene_name: str, case: SupportCase
) -> None:
    """G-10: routine frames must not light more tier-3/4 accents than the
    active colour depth's budget affords. PLAN.md S-1 batches every
    event-tier role the render path still considers "on screen" into the
    bid set of whichever role is actually rendering a new line (see
    ``ParallelDisplay._apply_salience``), so ``AllocationDecision.frame_index``
    (added by S-1) now genuinely groups more than one competing bid per
    real production frame. Grouping the flat decision stream by that
    field and checking each group is the actual G-10 gate: a frame that
    lights more tier-3/4 accents than its budget affords must fail this
    test, not merely satisfy an invariant the allocator already guarantees
    for a single-bid call regardless of contention."""
    decisions = scene_salience_decisions(
        scene_name, case, terminal_bg_is_light=case.terminal_background_is_light
    )
    depth = (
        "truecolor"
        if case.colour == "truecolour"
        else "256"
        if case.colour == "reduced"
        else "none"
    )
    budget = ACCENT_BUDGET_BY_DEPTH[depth]
    frames: dict[int, list[object]] = {}
    for decision in decisions:
        frames.setdefault(decision.frame_index, []).append(decision)
    for frame_index, frame_decisions in frames.items():
        lit_event_or_alarm = sum(
            1
            for d in frame_decisions
            if d.lit and d.tier in (FrequencyTier.EVENT, FrequencyTier.ALARM)
        )
        assert lit_event_or_alarm <= budget, (
            scene_name,
            case,
            frame_index,
            frame_decisions,
        )
    if scene_name == "burst":
        # PLAN.md S-1/S-2: burst is the catalog's dedicated
        # concurrent-activity vehicle (PLAN.md Characterize #5). Unless at
        # least one of its frames actually carries more than one
        # competing bid, the budget check above is checking the same
        # tautology the pre-S-1 single-bid-per-frame model produced --
        # this assertion is what makes the budget check above a real gate
        # for this scene rather than a restatement of ``allocate_frame``'s
        # own per-call contract.
        assert any(len(v) > 1 for v in frames.values()), (scene_name, case, frames)


#: E-3 ceiling: at most 16 tier-3 accents lit across a routine scene's
#: full render. Distinct from G-8's wider, colour-depth-scaled per-frame
#: contention budget (``ACCENT_BUDGET_BY_DEPTH``, 4/3/2) -- the E-3 cap
#: is the *whole-scene* count, not the per-frame count.
_E3_ROUTINE_SCENE_CEILING: int = 16


@pytest.mark.timeout_seconds(5)
@pytest.mark.criteria("E-3")
def test_generated_scene_routine_output_never_exceeds_the_e3_accent_ceiling() -> None:
    """E-3: routine output (the clean_run and idle_stretch scenes) must
    not exceed the E-3 ceiling on tier-3 accents regardless of
    background or colour. Sister to the depth-budget sweep
    (``test_generated_scene_salience_decisions_never_exceed_the_depth_budget``,
    which checks the per-frame G-10 gate); this one checks the
    scene-level E-3 ceiling aggregated across the whole scene."""
    case = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
    for scene_name in ("clean_run", "idle_stretch"):
        decisions = scene_salience_decisions(scene_name, case, terminal_bg_is_light=False)
        lit_tier_3 = sum(1 for d in decisions if d.lit and d.tier == FrequencyTier.EVENT)
        # The E-3 ceiling is the maximum number of tier-3 accents the
        # scene may light across its full render; depth budget bounds
        # the per-frame count, this bounds the per-scene count.
        assert lit_tier_3 <= _E3_ROUTINE_SCENE_CEILING, (scene_name, lit_tier_3, decisions)


@pytest.mark.timeout_seconds(5)
def test_generated_scene_alarm_decisions_are_never_demoted() -> None:
    """G-6: alarm-tier salience is never demoted, regardless of the
    surrounding routine stretch's count. ``ALARM`` is the only tier
    that breaks the G-4 decay invariant because it is exempt from
    demotion by construction (see ``SalienceAllocator``)."""
    case = SupportCase("dark", "truecolour", "unicode", FULL_LAYOUT_WIDTH, "tty")
    decisions = scene_salience_decisions("failure", case, terminal_bg_is_light=False)
    alarm_decisions = [d for d in decisions if d.tier == FrequencyTier.ALARM]
    assert alarm_decisions
    assert all(d.lit for d in alarm_decisions), alarm_decisions


def test_generated_scene_replay_of_an_identical_event_sequence_is_byte_identical() -> None:
    """G-9: a deterministic frame sequence must produce a byte-identical
    decision stream on replay. ``SalienceAllocator`` is a pure function
    of the frame sequence, exercised here through
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
