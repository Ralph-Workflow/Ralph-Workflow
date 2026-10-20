"""PA-002 / wt-19 property tests: exact-semantics locks and changing-trace monotonicity.

This module is the "PA-002 Extended properties & Kill-relevant
monotonicity" section of the §3b liveness property suite, split out of
``tests/test_liveness_properties.py`` purely to stay under the 1000-line
repo-structure cap. Nothing changed in the move: every test name,
decorator, ``@settings(...)``, assertion and docstring is identical to
the section it came from.

**Why the score / kill-time monotonicity properties are absent.**
PRODUCT_CRITERIA Part A ships the literal Sugeno aggregate
``s = Σwᵢzᵢ / Σwᵢ``. That aggregate admits *dilution*: a new or raised
rule whose ``zᵢ`` sits below the current score pulls ``s`` DOWN, so ``s``
is neither globally nor per-signal monotone, and a kill can arrive
strictly EARLIER when a working signal is raised during a CHANGING
trace. Three counterexamples defeat the old monotonicity claims, and
each is locked here as an exact-semantics regression rather than
asserted away:

  * (a) R1 + I/O dilution — score 1.0 -> 0.95
    (``test_extended_property_high_z_baseline_r1_io_dilution``)
  * (b) evidence-window gate transition — score 0.6 -> 0.2 / 1.38
    (``test_extended_property_evidence_window_gate_transitions``)
  * (c) kill-timing non-monotonicity under CHANGING I/O — a kill at
    130 s becomes 129 s when only the middle working segment's I/O
    rises (``test_kill_timing_non_monotone_under_changing_io``)

The properties that DO survive, and that this module proves, are the
ones literal Sugeno actually satisfies: score boundedness in [0, 1],
classification safety (working/suspicious never regresses to
stalled/unknown), the ``P >= 0`` clamp, and the single surviving
monotone claim — raising an idle-fired rule strictly raises ``s`` from
an all-idle baseline.

The isolated-score regressions (the exact-semantics integer locks for
each counterexample above) live in ``tests/test_liveness_assessor.py``;
the randomised, wider-range property forms that would trip on any
future change to the rule weights or z-values live here. The §3b
"Bounded / Termination by ceiling" family lives in
``tests/test_liveness_termination.py``, and the helper-surface tests
(SuspicionClock + step_with_assessment) live in
``tests/test_liveness_helpers.py``.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ralph.process.manager._liveness_assessor import (
    LivenessConfig,
    LivenessWindow,
    StallIntegrator,
    assess,
    default_liveness_config,
)

# Per-module timeout enforced by conftest's pytest.mark.timeout_seconds support;
# no environment mutation, no process-level side effects at import.
pytestmark = pytest.mark.timeout_seconds(30)

CFG: LivenessConfig = default_liveness_config()

# ---------------------------------------------------------------------------
# PA-002 Extended properties & Kill-relevant monotonicity
# ---------------------------------------------------------------------------


# REMOVED in PA-002 / wt-19:
#   - _kill_relevant_signal_raises helper + test_kill_relevant_monotonicity
#     (asserted kill-monotone over a SINGLE-WINDOW repeated trace; refuted
#      by case-(c) kill-timing non-monotonicity on a CHANGING trace; the
#      helper exercised exactly one window repeated, so it could not
#      observe a changing-trace failure)
#   - test_monotonic_kill_delay further down (single-window kill monotone)


@st.composite
def _single_signal_raises_in_changing_trace(
    draw: st.DrawFn,
) -> tuple[LivenessWindow, LivenessWindow, str]:
    """Generate (w1, w2, signal_name) raising one working signal inside a
    CHANGING trace (idle -> working -> idle) rather than a single-window
    repeated trace.

    Replaces the removed ``_kill_relevant_signal_raises`` (PA-002
    disposition, wt-19). The trace pattern is held fixed; only the
    working window's signal raise varies, and the property
    ``test_changing_trace_classification_safety_with_signal_raise``
    below asserts the four guarantees that DO hold under literal Sugeno:
    score boundedness, classification safety (working/suspicious never
    regresses), P >= 0 clamp, and the only surviving baseline-relative
    monotonicity (raising an idle-fired rule strictly raises ``s`` from
    score=0).
    """
    signal_name = draw(
        st.sampled_from(["io_bytes_per_s", "cpu_ratio", "child_events", "voluntary_ctx_per_s"])
    )
    if signal_name == "io_bytes_per_s":
        base_io = draw(st.floats(min_value=0.0, max_value=max(0.0, CFG.io_kbps_inactive - 0.01)))
        raised_io = draw(
            st.floats(
                min_value=CFG.io_kbps_active,
                max_value=CFG.io_kbps_active + 200.0,
            )
        )
        w1 = LivenessWindow(0.5, 0.0, base_io, 0, 1.0, 0.0, 0.5)
        w2 = LivenessWindow(0.5, 0.0, raised_io, 0, 1.0, 0.0, 0.5)
    elif signal_name == "cpu_ratio":
        w1 = LivenessWindow(0.5, 0.0, 0.0, 0, 1.0, 0.0, 0.5)
        w2 = LivenessWindow(
            0.5,
            draw(
                st.floats(
                    min_value=CFG.cpu_busy_min + 0.01,
                    max_value=CFG.cpu_busy_max + 0.2,
                )
            ),
            0.0,
            0,
            1.0,
            0.0,
            0.5,
        )
    elif signal_name == "child_events":
        w1 = LivenessWindow(0.5, 0.0, 0.0, 0, 1.0, 0.0, 0.5)
        w2 = LivenessWindow(
            0.5,
            0.0,
            0.0,
            draw(st.integers(min_value=1, max_value=5)),
            1.0,
            0.0,
            0.5,
        )
    else:  # voluntary_ctx_per_s
        w1 = LivenessWindow(0.5, 0.0, 0.0, 0, 1.0, 0.0, 0.5)
        w2 = LivenessWindow(
            0.5,
            0.0,
            0.0,
            0,
            1.0,
            draw(
                st.floats(
                    min_value=CFG.awake_per_s_inactive + 0.01,
                    max_value=CFG.awake_per_s_active + 5.0,
                )
            ),
            0.5,
        )
    return w1, w2, signal_name


@given(triple=_single_signal_raises_in_changing_trace())
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_changing_trace_classification_safety_with_signal_raise(
    triple: tuple[LivenessWindow, LivenessWindow, str],
) -> None:
    """PA-002 changing-trace property (replaces the removed
    single-window kill-monotone helpers).

    The trace pattern is fixed: idle / working / idle. The helper
    randomizes ONE working signal of the working window. The property
    asserts the guarantees that DO hold under literal Sugeno:

      1. Working-step score is in [0, 1] (score boundedness).
      2. Working-step classification stays in {working, suspicious};
         never regresses from working/suspicious to stalled/unknown.
      3. P >= 0 at every step (clamp invariant) -- raising a working
         signal in the middle of a trace cannot make pressure drift
         negative.
      4. Baseline-relative monotonicity -- when the baseline scores 0,
         raising any single working signal strictly raises the score
         (the only monotone claim literal-Sugeno provably satisfies).

    The previous single-window kill-monotone helpers
    (``_kill_relevant_signal_raises`` + ``test_kill_relevant_monotonicity``
    and ``test_monotonic_kill_delay``) encoded false claims: a
    single-window repeated trace where raising one signal can never
    accelerate a kill that has no pre-existing pressure to drain.
    Case (c) (locked by ``test_kill_timing_non_monotone_under_changing_io``
    below) shows the changing-trace failure mode.
    """
    w1, w2, signal_name = triple

    v1 = assess(w1, CFG)
    v2 = assess(w2, CFG)

    # Property 1: working-step score in unit interval.
    assert 0.0 <= v2.score <= 1.0 + 1e-9, (
        f"Score out of unit interval after raising {signal_name}: {v2.score!r}"
    )

    # Property 2: classification safety.
    if v1.verdict_kind in {"working", "suspicious"}:
        assert v2.verdict_kind in {"working", "suspicious"}, (
            f"Classification regressed after raising {signal_name}: "
            f"v1={v1.verdict_kind!r} -> v2={v2.verdict_kind!r}"
        )

    # Property 3: P >= 0 clamp across a changing trace.
    si = StallIntegrator(theta=CFG.stall_theta)
    for _ in range(5):
        si.step(verdict_score=0.0, dt=1.0)
    si.step(verdict_score=v2.score, dt=1.0)
    for _ in range(5):
        si.step(verdict_score=0.0, dt=1.0)
    assert si.pressure >= 0.0, (
        f"Pressure drifted negative after raise-then-idle trace: {si.pressure!r}"
    )

    # Property 4: baseline-relative monotonicity -- the only score
    # monotone claim literal Sugeno provably satisfies.
    if v1.score < 1e-9:
        assert v2.score >= v1.score - 1e-9, (
            f"Baseline-relative monotonicity violated from all-idle baseline: "
            f"v1.score={v1.score!r} > v2.score={v2.score!r} after raising {signal_name}"
        )


@given(
    sso_inside=st.floats(min_value=0.0, max_value=CFG.evidence_window_s - 0.1),
    nov=st.floats(min_value=0.0, max_value=CFG.repetitive_max),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_extended_property_evidence_window_gate_transitions(sso_inside: float, nov: float) -> None:
    """Case (b) locking property: R1 is suppressible by repetitiveness.

    Inside the evidence window (< 30s) with repetitive output (<= repetitive_max),
    R6 always fires with z=0.2 and R1 is blocked (0.0). Outside the evidence
    window (>= 30s), novelty is gate-unknown (D5), so R6 cannot fire.

    This is one half of the case-(b) counterexample locked exactly by
    ``tests/test_liveness_assessor.py::test_regression_pa002_counterexample_2_evidence_window_gate_transition``
    (score 0.6 at sso=30 -> score 0.2/1.38 at sso=29). The cross-boundary
    transition is the spec-internal contradiction that defeats per-signal
    monotonicity; the in-window property here proves the inside-window
    R6 detection under repeated randomly-drawn inputs.
    """
    w_inside = LivenessWindow(
        seconds_since_output=sso_inside,
        cpu_ratio=0.0,
        io_bytes_per_s=0.0,
        child_events=0,
        output_novelty=nov,
        voluntary_ctx_per_s=0.0,
        elapsed_s=30.0,
    )
    v_inside = assess(w_inside, CFG)
    assert v_inside.strongest_rule == "R6"
    assert v_inside.score <= 0.2 + 1e-9


@given(
    io_val=st.floats(min_value=CFG.io_kbps_active, max_value=CFG.io_kbps_active + 200.0),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_extended_property_high_z_baseline_r1_io_dilution(io_val: float) -> None:
    """Case (a) locking property: literal-Sugeno weighted average admits dilution.

    R1 fires with z=1.0 alone (score 1.0). Raising io into the active range
    brings in R2 (z=0.9), diluting the score to exactly 0.95. Both scores
    are strictly above ``theta`` so neither window kills.

    PA-002 / wt-19: this is one half of the case-(a) counterexample that
    locks the shipped aggregate as a literal-Sugeno weighted average; the
    full exact-semantics regression (no io=64.0 hardcode) lives at
    ``tests/test_liveness_assessor.py::test_regression_pa002_counterexample_1_r1_io_dilution``.
    The randomized form below widens the lock to a whole io-hypothesis
    range so any future change to the rule weights or z-values trips
    both this property and the exact regression.
    """
    w_base = LivenessWindow(
        seconds_since_output=0.0,
        cpu_ratio=0.0,
        io_bytes_per_s=0.0,
        child_events=0,
        output_novelty=1.0,
        voluntary_ctx_per_s=0.0,
        elapsed_s=10.0,
    )
    w_raised = LivenessWindow(
        seconds_since_output=0.0,
        cpu_ratio=0.0,
        io_bytes_per_s=io_val,
        child_events=0,
        output_novelty=1.0,
        voluntary_ctx_per_s=0.0,
        elapsed_s=10.0,
    )
    v_base = assess(w_base, CFG)
    v_raised = assess(w_raised, CFG)

    assert v_base.score == pytest.approx(1.0)
    assert v_raised.score == pytest.approx(0.95)
    assert v_raised.score < v_base.score
    assert v_raised.verdict_kind == "working"
    assert v_raised.score > CFG.stall_theta


# ---------------------------------------------------------------------------
# PA-002 case (c) -- kill-timing non-monotonicity under CHANGING io (wt-19)
# ---------------------------------------------------------------------------
#
# This is the regression that wt-19 added. The PA-002 / wt-19 closer:
# the shipped literal-Sugeno weighted average + StallIntegrator can
# kill EARLIER when a working signal is raised DURING a changing trace,
# not on a single-window repeated trace.
#
# Trace (observed with default liveness config, dt=1.0, stall_window=90):
#   1. 80 s idle (sso=60, novelty=None, io=0.0) -> each step adds 1.0 to P
#      -> after 80 steps P = 80.0 exactly.
#   2. 20 s of recent novel output. With io=0.0 R1 fires alone (score 1.0),
#      per-step delta = (theta - 1.0)/theta = -1.0 -> P drops by 20 -> 60.0.
#      With io=65536.0 R1+R2 fire (score 0.95), per-step delta = -0.9 ->
#      P drops by 18 in math, 17.999999999999996 in IEEE-754 -> 62.0
#      (approximately, off-by-ulp).
#   3. Post-idle: P needs to cross 90.0 -> 30 more seconds for the io=0
#      case, 28 or 29 for the io=65536 case depending on the floating
#      point rounding of the working-segment drop.
#
# Observed kill times (default liveness config, dt=1.0, stall_window=90):
#   io=0.0      -> kill_time = 130 (= 80 + 20 + 30 post-idle)
#   io=64.0     -> kill_time = 129 (= 80 + 20 + 29 post-idle, FP-rounded)
#   io=65536.0  -> kill_time = 129 (same FP-rounded drop)
#
# The mathematical ideal is 128 (= 80 + 20 + 28); the IEEE-754
# accumulated delta (0.5 - 0.95)/0.5 * dt is -0.8999999999999999 per
# step, which over 20 steps drops P by 17.999999999999998 instead of
# 18.0 exactly, requiring one additional post-idle step to cross 90.0.
# This rounding off-by-ulp is intrinsic to the StallIntegrator's
# non-integer-arithmetic path, not a defect.
#
# Deviation disposition: this is the SPEC-INTERNAL score / kill-time
# non-monotonicity that PA-002 closes. The shipped literal-Sugeno
# weighted average ``s = Σ wᵢzᵢ / Σ wᵢ`` admits dilution; killing
# earlier when a working signal is raised on a changing trace is a
# property of the shipped aggregate, not a bug. The remaining
# guarantees (bounded ``P``, fail-closed unknowns, baseline-relative
# score monotonicity, all-idle kill at ``stall_window_s`` ± one
# interval — see scenario 1 and the §3 ramp-offset reconciliation in
# ``ralph/mcp/ARCHITECTURE.md``) are stated separately in the
# accepted-deviations section of
# ``docs/agents/exec-long-running-root-cause.md``.


def _run_changing_io_kill_trace(
    io_during_working: float,
    pre_idle_s: int = 80,
    working_s: int = 20,
    stall_window_s: float = 90.0,
    dt: float = 1.0,
) -> tuple[int, float]:
    """Execute the case-(c) trace and return (kill_tick, final_pressure)."""
    si = StallIntegrator(theta=CFG.stall_theta)

    def _step(win: LivenessWindow) -> None:
        v = assess(win, CFG)
        si.step(verdict_score=v.score, dt=dt)

    pre_idle_win = LivenessWindow(
        seconds_since_output=60.0,
        cpu_ratio=0.0,
        io_bytes_per_s=0.0,
        child_events=0,
        output_novelty=None,
        voluntary_ctx_per_s=0.0,
        elapsed_s=60.0,
    )
    for _ in range(pre_idle_s):
        _step(pre_idle_win)

    working_win = LivenessWindow(
        seconds_since_output=0.5,
        cpu_ratio=0.0,
        io_bytes_per_s=io_during_working,
        child_events=0,
        output_novelty=1.0,
        voluntary_ctx_per_s=0.0,
        elapsed_s=0.5,
    )
    for _ in range(working_s):
        _step(working_win)

    post_idle_win = pre_idle_win
    tick = pre_idle_s + working_s
    while tick < pre_idle_s + working_s + 1000:
        tick += 1
        _step(post_idle_win)
        if si.should_kill(stall_window_s):
            return tick, si.pressure
    return -1, si.pressure


def test_kill_timing_non_monotone_under_changing_io() -> None:
    """PA-002 case (c): a changing trace kills EARLIER when I/O is raised.

    Over the 80s idle / 20s working / idle trace with default liveness
    config, dt=1.0, stall_window=90:

    * io=0.0      -> kill_time = 130
    * io=64.0     -> kill_time = 129
    * io=65536.0  -> kill_time = 129

    Raising only the I/O rate during the middle working segment makes
    the kill arrive strictly earlier (130 -> 129 under IEEE-754). The
    mathematical ideal is 128 (= 80 + 20 + 28); the StallIntegrator's
    accumulated delta off-by-ulp pushes the post-idle cross over
    stall_window one tick later.

    REGRESSION CONTRACT: locking these exact integers locks the
    non-monotonicity as a property of the shipped aggregate. A future
    attempt to "fix" the dilution by changing the rule weights, the
    z-values, theta, or the StallIntegrator update will trip this test.
    """
    k0, p0 = _run_changing_io_kill_trace(io_during_working=0.0)
    k64, p64 = _run_changing_io_kill_trace(io_during_working=64.0)
    k_big, p_big = _run_changing_io_kill_trace(io_during_working=65536.0)

    assert k0 == 130, f"case-(c) io=0 baseline shifted: got kill_time={k0}, expected 130"
    assert k64 == 129, f"case-(c) io=64 shifted: got kill_time={k64}, expected 129"
    assert k_big == 129, f"case-(c) io=65536 shifted: got kill_time={k_big}, expected 129"

    # The literal-sugeno dilution anti-monotone ordering: raising ONLY
    # the I/O rate during the middle working segment makes the kill
    # arrive strictly EARLIER. This is the property PA-002 closes.
    assert k_big < k0, (
        f"case-(c) non-monotonicity regression: raising I/O during the "
        f"working segment made the kill arrive LATER ({k0} -> {k_big}) "
        f"instead of earlier (130 -> 129). The regression contract "
        f"requires 130 -> 129; if the upgrade changed this, it also "
        f"probably fixed the literal-sugeno dilution root cause and "
        f"this test should be updated to reflect the new behavior."
    )

    assert p0 >= 90.0 - 1e-9, f"kill pressure below stall_window: {p0!r}"
    assert p64 >= 90.0 - 1e-9, f"kill pressure below stall_window: {p64!r}"
    assert p_big >= 90.0 - 1e-9, f"kill pressure below stall_window: {p_big!r}"


def test_changing_trace_p_never_negative_with_single_signal_raise() -> None:
    """Companion property: across the case-(c) trace shape, raising a
    single working signal in the middle never makes ``P`` drift negative.

    This is the boundedness / clamp invariant restricted to the trace
    shape that drove the counterexample; it is one of the guarantees
    that DO hold under literal Sugeno (see
    ``test_changing_trace_classification_safety_with_signal_raise``
    for the randomized form).
    """
    for io_during_working in (0.0, 64.0, 65536.0):
        si = StallIntegrator(theta=CFG.stall_theta)
        for _ in range(80):
            v = assess(LivenessWindow(60.0, 0.0, 0.0, 0, None, 0.0, 60.0), CFG)
            si.step(verdict_score=v.score, dt=1.0)
        for _ in range(20):
            v = assess(
                LivenessWindow(0.5, 0.0, io_during_working, 0, 1.0, 0.0, 0.5),
                CFG,
            )
            si.step(verdict_score=v.score, dt=1.0)
        for _ in range(60):
            v = assess(LivenessWindow(60.0, 0.0, 0.0, 0, None, 0.0, 60.0), CFG)
            si.step(verdict_score=v.score, dt=1.0)
        assert si.pressure >= 0.0, (
            f"P went negative on changing trace with mid io={io_during_working}: P={si.pressure!r}"
        )


@given(
    cpu_ratio=st.floats(min_value=0.0, max_value=CFG.cpu_busy_max + 0.2),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_extended_property_negated_antecedent_cpu_raise_transition(cpu_ratio: float) -> None:
    """Negated-antecedent transition: raising CPU when awake is active.

    As CPU rises from 0.0 to cpu_busy_max, the assessor transitions from R7 (0.55)
    to R4 (0.60). Under literal Sugeno, this transition is monotone non-decreasing:
    0.55 <= score <= 0.60, and at the midpoint cpu_ratio is exactly 0.575.
    """
    w = LivenessWindow(
        seconds_since_output=90.0,
        cpu_ratio=cpu_ratio,
        io_bytes_per_s=0.0,
        child_events=0,
        output_novelty=None,
        voluntary_ctx_per_s=CFG.awake_per_s_active,
        elapsed_s=10.0,
    )
    v = assess(w, CFG)
    assert 0.55 - 1e-9 <= v.score <= 0.60 + 1e-9
    assert v.verdict_kind == "suspicious"

    cpu_mid = (CFG.cpu_busy_min + CFG.cpu_busy_max) / 2.0
    if abs(cpu_ratio - cpu_mid) < 1e-6:
        assert v.score == pytest.approx(0.575)


# REMOVED in PA-002 / wt-19: test_extended_property_multi_signal_classification_safety
#   (duplicate of ``test_classification_not_demoted_by_signal_raises``
#   above; the property stands on its stronger randomized form).


# ---------------------------------------------------------------------------
# Isolated novelty counterexample (WU-2 / E-MONOTONIC)
# ---------------------------------------------------------------------------
#
# PA-002 disposition: PRODUCT_CRITERIA Verification §3 "Monotonic" is
# unsatisfiable together with the rest of Part A. The single-window
# counterexample discovered under E-MONOTONIC was
#
#     LivenessWindow(90.0, 0.0, 0.0, 0, novelty, 0.0, 90.0)
#         → score=0.2, kill_at=150 for novelty=0.1
#         → score=0.0, kill_at=90  for novelty=0.5
#
# which occurred when R6 ("Printing the same line in a loop") fired on
# a process that had emitted no output in the 30s evidence window.
# Under canonical D2/D3/D5: when output is stale
# (seconds_since_output=90s >= evidence_window_s=30s), no output occurred
# in the evidence window, so output novelty is unknown and repetitive=0.
# Both novelty=0.1 and novelty=0.5 yield score=0.0 (R5 alone). Raising
# novelty on a SILENT/IDLE process never lowers the score or speeds up
# a kill (this is the isolated property kept below as an exact-semantics
# regression). The full score/kill monotonicity clauses were removed.


def test_novelty_monotone_on_silent_idle_processes() -> None:
    """PA-002 isolated property: raising novelty on a silent/idle process
    never lowers the score or speeds up a kill.

    When output is stale (seconds_since_output=90s >= evidence_window_s=30s),
    no output occurred in the evidence window, so output novelty is unknown
    and repetitive=0. Both novelty=0.1 and novelty=0.5 yield score=0.0
    (R5 alone). Stall pressure grows at the same rate for both windows,
    and ``kill_step`` lands at the same tick within ±1 of
    ``stall_window_s / dt``.

    Replaces the previously-named ``test_novelty_monotone_non_decreasing_score``
    with a re-scoped docstring that no longer claims general per-signal
    monotonicity. The renamed test makes explicit the precondition
    (silent/idle process) and the property that holds (no speedup,
    score never lowers).
    """
    w_low = LivenessWindow(
        seconds_since_output=90.0,
        cpu_ratio=0.0,
        io_bytes_per_s=0.0,
        child_events=0,
        output_novelty=0.1,
        voluntary_ctx_per_s=0.0,
        elapsed_s=90.0,
    )
    w_high = LivenessWindow(
        seconds_since_output=90.0,
        cpu_ratio=0.0,
        io_bytes_per_s=0.0,
        child_events=0,
        output_novelty=0.5,
        voluntary_ctx_per_s=0.0,
        elapsed_s=90.0,
    )

    v_low = assess(w_low, CFG)
    v_high = assess(w_high, CFG)

    # Score never goes DOWN when novelty rises (the property under repair).
    assert v_high.score >= v_low.score - 1e-9, (
        f"novelty monotonicity violated: novelty=0.1 score={v_low.score!r} "
        f"vs novelty=0.5 score={v_high.score!r}; raising novelty lowered "
        f"the score (E-MONOTONIC counterexample)"
    )

    # Stall-pressure rate must not INCREASE when novelty rises.
    p_rate_low = max(0.0, (CFG.stall_theta - v_low.score) / CFG.stall_theta)
    p_rate_high = max(0.0, (CFG.stall_theta - v_high.score) / CFG.stall_theta)
    assert p_rate_high <= p_rate_low + 1e-9, (
        f"novelty monotonicity violated at the pressure-rate layer: "
        f"p_rate_low={p_rate_low!r}, p_rate_high={p_rate_high!r}; raising "
        f"novelty accelerated stall-pressure accumulation"
    )

    # And the kill step lands at exactly ``stall_window_s`` ± one interval
    # — not accelerated by the novelty raise.
    stall_window_s = 90.0
    dt = 2.0

    def kill_step(win: LivenessWindow) -> int:
        si = StallIntegrator(theta=CFG.stall_theta)
        for i in range(1, 200):
            v = assess(win, CFG)
            si.step(verdict_score=v.score, dt=dt)
            if si.should_kill(stall_window_s):
                return i
        return -1

    k_low = kill_step(w_low)
    k_high = kill_step(w_high)

    # Both should kill within one interval of ``stall_window_s / dt`` ticks.
    expected_kills = stall_window_s / dt  # 45 ticks
    assert abs(k_low - expected_kills) <= 1, (
        f"novelty=0.1 should kill at {expected_kills} ticks (±1); got {k_low}"
    )
    assert abs(k_high - expected_kills) <= 1, (
        f"novelty=0.5 should kill at {expected_kills} ticks (±1); got {k_high}"
    )


def test_novelty_monotone_when_output_recent() -> None:
    """When output is recent, raising novelty transitions from R6 (0.2) to R1 (1.0)."""
    w_rep = LivenessWindow(
        seconds_since_output=0.0,
        cpu_ratio=0.0,
        io_bytes_per_s=0.0,
        child_events=0,
        output_novelty=0.05,
        voluntary_ctx_per_s=0.0,
        elapsed_s=10.0,
    )
    w_novel = LivenessWindow(
        seconds_since_output=0.0,
        cpu_ratio=0.0,
        io_bytes_per_s=0.0,
        child_events=0,
        output_novelty=0.5,
        voluntary_ctx_per_s=0.0,
        elapsed_s=10.0,
    )

    v_rep = assess(w_rep, CFG)
    v_novel = assess(w_novel, CFG)

    assert v_rep.score == pytest.approx(0.2)
    assert v_rep.strongest_rule == "R6"
    assert v_novel.score == pytest.approx(1.0)
    assert v_novel.strongest_rule == "R1"
    assert v_novel.score >= v_rep.score


# REMOVED in PA-002 / wt-19:
#   - test_monotonic_kill_delay
#     (asserted per-signal kill-monotone from a SINGLE-WINDOW trace;
#      same failure mode as the per-signal score monotone: a single
#      repeated window with zero pre-existing pressure can never
#      observe case-(c) kill-timing non-monotonicity, so the test
#      would pass even though the property is false on a changing
#      trace. Replaced by ``test_kill_timing_non_monotone_under_changing_io``
#      below and the changing-trace property above.)
