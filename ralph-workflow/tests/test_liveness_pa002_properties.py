"""PA-002 / wt-19 property tests: exact-semantics locks and changing-trace monotonicity.

This module is the "PA-002 Extended properties & Kill-relevant
monotonicity" section of the §3b liveness property suite, split out of
``tests/test_liveness_properties.py`` purely to stay under the 1000-line
repo-structure cap. Nothing changed in the move: every test name,
decorator, ``@settings(...)``, assertion and docstring is identical to
the section it came from.

**Why the score / kill-time monotonicity properties are absent.**
PRODUCT_CRITERIA Verification §3 requires "raising any working signal
never lowers `s` or speeds up a kill". That criterion is
**UNSATISFIABLE jointly with the prescribed Part A rule table**, and this
module carries the reconciliation rather than a weakened assertion of it.
PART A ships the literal Sugeno aggregate ``s = Σwᵢzᵢ / Σwᵢ``. That
aggregate admits *dilution*: a new or raised rule whose ``zᵢ`` sits below
the current score pulls ``s`` DOWN, so ``s`` is neither globally nor
per-signal monotone, and a kill can arrive strictly EARLIER when a working
signal is raised during a CHANGING trace. Three counterexamples defeat the
old monotonicity claims, and each is locked here as an exact-semantics
DEVIATION CONTRACT rather than asserted away:

  * (a) R1 + I/O dilution — score 1.0 -> 0.95
    (``test_extended_property_high_z_baseline_r1_io_dilution``)
  * (b) evidence-window gate transition — score 0.6 -> 0.2 / 1.38
    (``test_extended_property_evidence_window_gate_transitions``)
  * (c) kill-timing non-monotonicity under CHANGING I/O — a kill at
    130 s becomes 129 s when only the middle working segment's I/O
    rises (``test_kill_timing_non_monotone_under_changing_io``)

WU-2 additionally proved a SECOND spec-internal collision, about kill
TIMING rather than the score: criterion 3 / trace-library scenario 8
("``stalled`` at ``stall_window`` after activity stops") is unreachable
for every aggregate and every theta under the mandated ``OutputRecent``
breakpoints, with proven floor ``t_kill >= last_output +
EXEC_OUTPUT_RECENT_MAX_S + stall_window``. Locked executably by the three
``PROOF 1`` tests at the end of this module; full proofs are PROOF 1 and
PROOF 2 in the module docstring of ``ralph.process.manager._liveness_assessor``.

The "AUTHORITATIVE DEVIATION CONTRACT" block further down carries the
proof that this is SPEC-INTERNAL rather than an implementation defect: the
membership ramps are exonerated (the drop holds at every interior ramp
point, ``ds/da = -0.1/(1+a)² < 0``), the literal rule set is the root
cause, and the normalized weighted average is the amplifier.

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

import dataclasses

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ralph import timeout_defaults as _td
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
# AUTHORITATIVE DEVIATION CONTRACT — case (c): kill-timing non-monotonicity
# under a CHANGING I/O rate (PA-002 / wt-19)
# ---------------------------------------------------------------------------
#
# PRODUCT_CRITERIA Verification §3 states, verbatim:
#
#     "Monotonic: raising any working signal never lowers `s` or speeds
#      up a kill."
#
# THIS BLOCK IS THE RECONCILIATION of that criterion against Part A.
# The criterion is UNSATISFIABLE JOINTLY WITH THE PRESCRIBED RULE TABLE,
# and the test below is a deliberate, LOCKED deviation contract — NOT a
# regression awaiting a "fix". Three independent reasons, in increasing
# order of force:
#
# 1. (iii) THE MEMBERSHIP RAMPS ARE EXONERATED. The failure is not a
#    breakpoint artifact. Hold every signal but `io` at the working-window
#    values (o=0.5 -> OutputRecent=1.0; n=1.0 -> Repetitive=0.0; c=t=w=0).
#    Then w1 = min(OutputRecent, NOT Repetitive) = 1.0 is CONSTANT and the
#    only varying weight is w2 = IoActive = a, so
#
#         s(a) = (1.0*1.0 + a*0.9) / (1.0 + a)
#         ds/da = [0.9(1+a) - (1 + 0.9a)] / (1+a)^2 = -0.1/(1+a)^2 < 0
#
#    for EVERY a in [0,1]: s is strictly DECREASING in `io` across the
#    whole ramp, not only at a breakpoint. Every strictly increasing
#    IoActive ramp (linear, sqrt, min(a,0.5), step) reproduces the drop,
#    so NO reparameterization of any membership ramp can fix this.
#
# 2. (i) THE LITERAL RULE SET IS THE ROOT CAUSE. Two structural facts of
#    Part A's table, both fixed by the spec and both binding on any
#    aggregate:
#      * R2 = IoActive carries NO negated antecedent and z2 = 0.9 <
#        z1 = 1.0. Admitting R2 to a weighted average currently pinned at
#        the MAXIMUM z (1.0) can only lower it. NOTHING IS SUPPRESSED in
#        this trace: w6 (R6) is 0.0 in every branch because
#        Repetitive = _repetitive(1.0) = 0.0 (novelty 1.0, and
#        o=0.5 < evidence_window_s=30 so the novelty gate is OPEN), and
#        w1 stays 1.0 throughout. The dilution is purely ADDITIVE.
#      * R1 = OutputRecent AND NOT Repetitive, while R6 = Repetitive AND
#        NOT IoActive. `w1` is therefore NOT monotone in the working
#        signal `o` across the evidence-window gate: improving recency
#        across o = 30 -> 29 flips NOT Repetitive from 1.0 to 0.0, so
#        w1 FALLS 0.6 -> 0.0 while w6 RISES 0 -> 1.0. The weight VECTOR
#        itself is not coordinate-wise monotone in a working signal.
#
# 3. (ii) THE NORMALIZED WEIGHTED AVERAGE s = Swz/Sw (Part A, D2) is the
#    AMPLIFIER: the normalization is what turns "a lower-z rule joined
#    the average" into "the average dropped", and it is what lets a
#    0.05 score drop translate into a one-tick earlier kill.
#
# THE REFUTATION THAT CLOSES EVERY CANDIDATE AGGREGATE (including ones
# that ARE monotone on case (a), e.g. strongest-product): the novel-gate
# pair is pinned by exact test assertion at BOTH ends —
#
#     tests/test_liveness_assessor.py:401   v_30.score == approx(0.6)
#     tests/test_liveness_assessor.py:414   v_29.score == approx(0.2/1.38)
#
# on windows that differ ONLY in `o` (30 -> 29: strictly better recency,
# the sole working signal raised). No aggregate whatsoever can return
# both values AND be monotone in `o`. The contradiction is SPEC-INTERNAL:
# Verification §3 "Monotonic" versus Part A's own R1/R6/evidence-gate
# loop-detection design. Lifting it requires editing PRODUCT_CRITERIA,
# not this code.
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
# DEVIATION DISPOSITION (authoritative, PA-002 / wt-19): this is the
# SPEC-INTERNAL score / kill-time non-monotonicity. The shipped
# literal-Sugeno weighted average ``s = Σ wᵢzᵢ / Σ wᵢ`` admits additive
# dilution; killing earlier when a working signal is raised on a changing
# trace is a property of the shipped aggregate, not a bug, and — per the
# three numbered reasons above — not repairable without editing
# PRODUCT_CRITERIA. The surviving guarantees (bounded ``P``, fail-closed
# unknowns, classification safety, baseline-relative score monotonicity,
# all-idle kill at ``stall_window_s`` ± one interval — see scenario 1) are
# asserted above and stated in the accepted-deviations section of
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
    """AUTHORITATIVE DEVIATION CONTRACT (case (c)): a changing trace
    kills EARLIER when I/O is raised. This is intentional, locked
    behavior — not a regression awaiting a fix.

    PRODUCT_CRITERIA Verification §3 "Monotonic: raising any working
    signal never lowers `s` or speeds up a kill" is UNSATISFIABLE
    jointly with the prescribed rule table; see the AUTHORITATIVE
    DEVIATION CONTRACT block above for the proof. In short: holding
    every other signal at its working-window value makes ``w1``
    (R1, z=1.0) constant at 1.0 and the only varying weight ``w2``
    (R2, z=0.9) equal to ``IoActive = a``, so

        s(a) = (1.0 + 0.9a) / (1 + a),   ds/da = -0.1/(1+a)^2 < 0

    for EVERY ``a`` in ``[0, 1]`` — the score falls strictly as I/O
    rises, at every interior point of the ramp, not merely at a
    breakpoint. No monotone reparameterization of any membership ramp
    removes it, and no aggregate can remove it while still returning
    the exact novel-gate pair 0.6 / 0.2/1.38 that
    ``tests/test_liveness_assessor.py`` pins at lines 401 and 414.

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

    CONTRACT: these exact integers are LOCKED. Changing the rule
    weights, the z-values, theta, or the StallIntegrator update so as
    to make the kill arrive later would NOT be a bug fix — it would
    silently rewrite Part A, and PRODUCT_CRITERIA Verification §3 would
    have to be re-reconciled in the same change. The test failing on
    exactly these numbers is the intended tripwire for that event.
    """
    k0, p0 = _run_changing_io_kill_trace(io_during_working=0.0)
    k64, p64 = _run_changing_io_kill_trace(io_during_working=64.0)
    k_big, p_big = _run_changing_io_kill_trace(io_during_working=65536.0)

    assert k0 == 130, f"case-(c) io=0 baseline shifted: got kill_time={k0}, expected 130"
    assert k64 == 129, f"case-(c) io=64 shifted: got kill_time={k64}, expected 129"
    assert k_big == 129, f"case-(c) io=65536 shifted: got kill_time={k_big}, expected 129"

    # The literal-Sugeno dilution ANTI-monotone ordering: raising ONLY the
    # I/O rate during the middle working segment makes the kill arrive
    # strictly EARLIER. This is the LOCKED deviation contract, asserted for
    # BOTH raised-I/O branches (not just the saturated one).
    assert k64 < k0, (
        f"case-(c) deviation contract broken: raising I/O to 64 KiB/s during "
        f"the working segment made the kill arrive LATER ({k0} -> {k64}) "
        f"instead of earlier (130 -> 129). The contract requires 130 -> 129; "
        f"a change here means PART A's rule weights, z-values, theta or the "
        f"StallIntegrator update moved, so PRODUCT_CRITERIA Verification §3 "
        f"must be re-reconciled in the same change."
    )
    assert k_big < k0, (
        f"case-(c) deviation contract broken: raising I/O during the "
        f"working segment made the kill arrive LATER ({k0} -> {k_big}) "
        f"instead of earlier (130 -> 129). The contract requires 130 -> 129; "
        f"a change here means PART A's rule weights, z-values, theta or the "
        f"StallIntegrator update moved, so PRODUCT_CRITERIA Verification §3 "
        f"must be re-reconciled in the same change."
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


# ---------------------------------------------------------------------------
# WU-2 — executable PROOF 1: the C3 / scenario-8 kill-time floor
# ---------------------------------------------------------------------------
#
# Criterion 3 / scenario 8 ask for ``stalled`` within ``stall_window + 1``
# sample interval of the LAST OUTPUT BYTE after a burst. PROOF 1 in
# ``ralph/process/manager/_liveness_assessor.py`` shows that is
# unsatisfiable under the mandated ``OutputRecent`` breakpoints, for
# EVERY aggregate and EVERY theta:
#
#   * for ``age <= EXEC_OUTPUT_RECENT_MAX_S`` the membership saturates at
#     1, so R1 alone fires with ``z1 = 1.0 > theta``, the D3 delta
#     ``dt * (theta - s) / theta`` is non-positive, and ``P`` cannot
#     rise at all (the burst's own samples leave ``P`` clamped at 0);
#   * every ``z`` lies in ``[0, 1]``, so ``s >= 0`` and the accrual rate
#     ``(theta - s) / theta <= 1`` for every ``theta > 0``;
#   * hence ``t_kill >= last_output + EXEC_OUTPUT_RECENT_MAX_S +
#     stall_window``.
#
# The three tests below make that proof executable: the bound itself,
# that the bound already sits OUTSIDE the criterion's tolerance (so the
# deviation is provable rather than a tuning miss), that it is
# theta-independent (so no D3 retune is an escape), and that the one
# retune which does reach the criterion trades it against criterion 2.


def _run_burst_then_idle_offset(
    config: LivenessConfig,
    *,
    burst_duration_s: float,
    stall_window_s: float,
    dt: float,
) -> tuple[float, float]:
    """Run a burst-then-all-idle trace; return ``(kill_time_s, last_output_s)``.

    Deterministic pure arithmetic — ``assess`` + ``StallIntegrator`` only,
    no clock, no I/O. Mirrors the burst signature of scenarios 8 and 13
    (fresh novel output, moderate CPU / I/O / awake) followed by the
    all-idle dead phase (everything zero, novelty unknown).
    """
    si = StallIntegrator(theta=config.stall_theta)
    t = 0.0
    last_output_s = 0.0
    while t < burst_duration_s:
        t += dt
        last_output_s = t
        si.step(
            verdict_score=assess(
                LivenessWindow(0.0, 0.2, 10.0, 0, 1.0, 1.0, t), config
            ).score,
            dt=dt,
        )
    while True:
        t += dt
        si.step(
            verdict_score=assess(
                LivenessWindow(t - last_output_s, 0.0, 0.0, 0, None, 0.0, t), config
            ).score,
            dt=dt,
        )
        if si.should_kill(stall_window_s):
            return t, last_output_s


def test_burst_then_idle_kill_never_beats_proof1_floor() -> None:
    """PROOF 1, locked executably: the burst-then-idle kill can never land
    earlier than ``last_output + EXEC_OUTPUT_RECENT_MAX_S + stall_window``,
    and that floor already sits outside the criterion's
    ``stall_window + 1 interval`` tolerance.

    Parametrised over the stall window and the burst length so the
    contract tracks the configured numbers rather than one magic pair. The
    assertions are a LOWER bound on the kill offset plus a strict
    inequality against the criterion deadline, so they can only ever
    tighten the shipped deviation, never conceal it. Nothing is weakened:
    the exact closed form at the shipped defaults
    (``burst_duration_s + 137.5``) is asserted separately by
    ``tests/test_exec_liveness_scenarios.py`` scenarios 8 and 13.
    """
    dt = 0.01
    for burst_duration_s, stall_window_s in (
        (10.0, 30.0),
        (30.0, 60.0),
        (30.0, 90.0),
        (120.0, 90.0),
        (5.0, 300.0),
    ):
        floor_offset_s = CFG.output_recent_min_s + stall_window_s
        kill_time_s, last_output_s = _run_burst_then_idle_offset(
            CFG,
            burst_duration_s=burst_duration_s,
            stall_window_s=stall_window_s,
            dt=dt,
        )
        offset_s = kill_time_s - last_output_s
        assert offset_s >= floor_offset_s - dt - 0.05, (
            f"PROOF 1 floor violated: burst={burst_duration_s}, "
            f"stall_window={stall_window_s} killed {offset_s!r} s after the last "
            f"output, below the proven floor "
            f"EXEC_OUTPUT_RECENT_MAX_S + stall_window = {floor_offset_s!r}"
        )
        # The floor is itself past the criterion's deadline, so the
        # literal C3 timing is unreachable rather than merely unmet.
        assert floor_offset_s > stall_window_s + dt


def test_proof1_floor_holds_for_every_theta() -> None:
    """PROOF 1's theta-independence claim, checked.

    Because every rule ``z`` lies in ``[0, 1]``, ``s >= 0`` and the D3
    accrual rate ``(theta - s) / theta <= 1`` for EVERY ``theta > 0``. So
    no retune of ``EXEC_STALL_THETA`` — including ``theta = 1.0``, the
    largest admissible value — can pull the burst-then-idle kill inside
    ``stall_window + 1 interval``. This is the step that makes "just raise
    theta" a dead end, and it is asserted rather than argued in prose.
    """
    burst_duration_s = 30.0
    stall_window_s = 90.0
    dt = 0.05
    floor_offset_s = CFG.output_recent_min_s + stall_window_s
    for theta in (0.1, 0.25, 0.5, 0.75, 1.0):
        config = dataclasses.replace(CFG, stall_theta=theta)
        kill_time_s, last_output_s = _run_burst_then_idle_offset(
            config,
            burst_duration_s=burst_duration_s,
            stall_window_s=stall_window_s,
            dt=dt,
        )
        offset_s = kill_time_s - last_output_s
        assert offset_s >= floor_offset_s - dt - 0.05, (
            f"PROOF 1 floor violated at theta={theta}: killed {offset_s!r} s "
            f"after the last output, below {floor_offset_s!r}"
        )
        assert offset_s > stall_window_s + dt, (
            f"theta={theta} reached the literal C3 deadline, so PROOF 1's "
            f"theta-independence claim is wrong and must be re-proved"
        )


def test_collapsed_output_recent_breakpoints_would_break_c2() -> None:
    """The rejected candidate, kept as a measured counterfactual.

    Collapsing the mandated ``OutputRecent`` breakpoints to ``1 at o <=
    0`` / ``0 at o >= 2`` is the ONLY change that lands the burst-then-idle
    kill inside criterion 3. It is refused because it re-creates cause #1
    of the whole feature: a suite that prints one line every 5 s with no
    CPU / I/O / tree / awake evidence is killed as idle instead of running
    to completion. Pinning the refusal here means a future attempt to
    "fix" C3 by retuning the breakpoints trips this test instead of
    quietly regressing criterion 2.
    """
    collapsed = dataclasses.replace(CFG, output_recent_min_s=0.0, output_recent_max_s=2.0)
    # Evaluate at the production sample interval so "one sample interval"
    # means what it means in the shipped runtime, not a synthetic grid.
    dt = _td.EXEC_LIVENESS_SAMPLE_INTERVAL_S
    horizon_s = 1200.0

    # (1) It does reach the literal criterion-3 deadline: the kill lands
    #     exactly at ``stall_window`` after the last output byte.
    kill_time_s, last_output_s = _run_burst_then_idle_offset(
        collapsed, burst_duration_s=30.0, stall_window_s=90.0, dt=dt
    )
    assert kill_time_s - last_output_s <= 90.0 + dt

    def _chatty_survival(config: LivenessConfig) -> float | None:
        """Kill time for a suite printing one line every 5 s, else ``None``."""
        si = StallIntegrator(theta=config.stall_theta)
        t = 0.0
        while t < horizon_s:
            t += dt
            si.step(
                verdict_score=assess(
                    LivenessWindow(t % 5.0, 0.0, 0.0, 0, 1.0, 0.0, t), config
                ).score,
                dt=dt,
            )
            if si.should_kill(90.0):
                return t
        return None

    # (2) ... but it kills that merely-chatty working process at ~218 s...
    killed_at_s = _chatty_survival(collapsed)
    assert killed_at_s is not None, (
        "the collapsed breakpoints no longer kill the chatty suite; the "
        "trade-off that refuses this candidate must be re-measured"
    )
    assert 90.0 < killed_at_s < horizon_s

    # (3) ... whereas the shipped breakpoints let it run the whole 20 min.
    assert _chatty_survival(CFG) is None, (
        "the shipped OutputRecent breakpoints must let a suite printing one "
        "line every 5 s survive 20 minutes (criterion 2)"
    )


# REMOVED in PA-002 / wt-19:
#   - test_monotonic_kill_delay
#     (asserted per-signal kill-monotone from a SINGLE-WINDOW trace;
#      same failure mode as the per-signal score monotone: a single
#      repeated window with zero pre-existing pressure can never
#      observe case-(c) kill-timing non-monotonicity, so the test
#      would pass even though the property is false on a changing
#      trace. Replaced by ``test_kill_timing_non_monotone_under_changing_io``
#      below and the changing-trace property above.)
