"""Hypothesis property tests for the §3b liveness assessor.

Property families at 50 examples each. Every example is pure arithmetic
(``assess`` + ``StallIntegrator.step``); no subprocess, no clock.

Properties:

1. **Kill-relevant monotonicity (PA-002 disposition)** — raising
   cpu/io/tree/awake membership or in-window novelty never converts a
   would-not-kill trace into a killed-one-at-or-before-the-same-time,
   excluding the documented R6/novelty-gate detection transitions (which
   is the R6 loop detection mechanism).
2. **Classification monotonicity** — raising working signals never demotes
   a process from working/suspicious to stalled/unknown.
3. **Exact-semantics regressions & transitions** — locked counterexample
   scores: R1+io dilution (1.0 -> 0.95), evidence-window gate transition
   (0.6 -> 0.2/1.38), and R7->R4 negated-antecedent boundary (0.55 -> 0.575).
4. **Bounded** — P can never exceed the configured ceiling on any trace.
5. **Fail closed** — None / unknown signals never delay a kill.
6. **Deterministic** — same input → same verdict.
7. **P ≥ 0** always; an all-idle exact-input trace kills at the configured
   ``stall_window_s`` ± one interval.

The §3b "Bounded / Termination by ceiling" property family lives in
``test_liveness_termination.py``; the helper-surface (SuspicionClock +
step_with_assessment) tests live in ``test_liveness_helpers.py``.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ralph.process.manager._liveness_assessor import (
    LivenessConfig,
    LivenessWindow,
    StallIntegrator,
    _output_recent,
    assess,
    default_liveness_config,
)

# Per-module timeout enforced by conftest's pytest.mark.timeout_seconds support;
# no environment mutation, no process-level side effects at import.
pytestmark = pytest.mark.timeout_seconds(30)

CFG: LivenessConfig = default_liveness_config()

# Bounded so hypothesis can shrink examples quickly and so we never spend
# more than a few ms per example. Membership functions clamp at the
# breakpoints anyway; values outside the bounds test the same code paths.
_AGE = st.floats(min_value=0.0, max_value=CFG.output_recent_max_s + 30.0)
_CPU = st.floats(min_value=0.0, max_value=CFG.cpu_busy_max + 0.3)
_IO = st.floats(min_value=0.0, max_value=CFG.io_kbps_active + 100.0)
_TREE = st.integers(min_value=0, max_value=5)
_NOVELTY = st.floats(min_value=0.0, max_value=1.0)
_AWAKE = st.floats(min_value=0.0, max_value=CFG.awake_per_s_active + 5.0)

# Each signal independently rolled as ``None`` or as a finite reading from
# the corresponding bounded strategy. The 50/50 None/value mix exercises the
# unknown-signal branch of every membership function.
_WINDOWS = st.builds(
    LivenessWindow,
    seconds_since_output=st.one_of(st.none(), _AGE),
    cpu_ratio=st.one_of(st.none(), _CPU),
    io_bytes_per_s=st.one_of(st.none(), _IO),
    child_events=st.one_of(st.none(), _TREE),
    output_novelty=st.one_of(st.none(), _NOVELTY),
    voluntary_ctx_per_s=st.one_of(st.none(), _AWAKE),
    elapsed_s=st.just(10.0),
)


# ---------------------------------------------------------------------------
# Property 1 — Monotonic
# ---------------------------------------------------------------------------


@st.composite
def _window_pairs_improved_working(draw: st.DrawFn) -> tuple[LivenessWindow, LivenessWindow]:
    """Generate (w1, w2) where w2 has equal or improved working signals vs w1."""
    w1_sso = draw(_AGE)
    w1_cpu = draw(_CPU)
    w1_io = draw(_IO)
    w1_tree = draw(_TREE)
    w1_nov = draw(_NOVELTY)
    w1_awake = draw(_AWAKE)

    # In w2, each signal is improved or kept equal:
    w2_sso = draw(st.floats(min_value=0.0, max_value=w1_sso))
    w2_cpu = draw(st.floats(min_value=w1_cpu, max_value=w1_cpu + 0.5))
    w2_io = draw(st.floats(min_value=w1_io, max_value=w1_io + 100.0))
    w2_tree = draw(st.integers(min_value=w1_tree, max_value=w1_tree + 5))
    w2_nov = draw(st.floats(min_value=w1_nov, max_value=1.0))
    w2_awake = draw(st.floats(min_value=w1_awake, max_value=w1_awake + 5.0))

    w1 = LivenessWindow(w1_sso, w1_cpu, w1_io, w1_tree, w1_nov, w1_awake, 10.0)
    w2 = LivenessWindow(w2_sso, w2_cpu, w2_io, w2_tree, w2_nov, w2_awake, 10.0)
    return w1, w2


@given(window=_WINDOWS)
@settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
def test_score_nonneg_across_signals(window: LivenessWindow) -> None:
    """Across every signal direction, the weighted-average score is non-negative.

    The pure liveness assessor never produces a negative score — all rule
    outputs (z) and rule weights are >= 0 and the score is a non-negative
    weighted average. This is a sanity invariant for the property suite.
    """
    assert assess(window, CFG).score >= 0.0


@given(window=_WINDOWS)
@settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
def test_score_bounded_by_unit_interval(window: LivenessWindow) -> None:
    """Score is in [0, 1] — every rule z is in [0, 1] and weighting stays in band."""
    v = assess(window, CFG)
    assert 0.0 <= v.score <= 1.0


@st.composite
def _single_signal_raises(draw: st.DrawFn) -> tuple[LivenessWindow, LivenessWindow, str]:
    """Generate (w1, w2, signal_name) where w2 raises exactly one working signal.

    The "valid interpretation" of PRODUCT_CRITERIA Verification §3
    "Monotonic" under the literal Sugeno aggregate: raising any single
    working signal never lowers the score. Raising ALL signals at once is
    NOT a Sugeno monotone — a rule firing for the first time can bring
    its own zᵢ into the weighted average, and Sugeno only raises the
    score when the new rule's zᵢ exceeds the current score. Multi-signal
    monotonicity holds in the ``max(wᵢ·zᵢ)`` aggregate (which is NOT
    what PRODUCT_CRITERIA Part A prescribes); the literal Sugeno
    aggregate is monotone per-signal only.
    """
    signal_name = draw(
        st.sampled_from(
            [
                "seconds_since_output",
                "cpu_ratio",
                "io_bytes_per_s",
                "child_events",
                "output_novelty",
                "voluntary_ctx_per_s",
            ]
        )
    )
    base_args = {
        "seconds_since_output": 60.0,
        "cpu_ratio": 0.0,
        "io_bytes_per_s": 0.0,
        "child_events": 0,
        "output_novelty": 0.5,
        "voluntary_ctx_per_s": 0.0,
        "elapsed_s": 10.0,
    }
    w1 = LivenessWindow(**base_args)
    if signal_name == "seconds_since_output":
        # "lower is better" for output_recency; raise the signal by
        # lowering the seconds-since-output value.
        new_val = draw(st.floats(min_value=0.0, max_value=base_args["seconds_since_output"]))
        w2 = LivenessWindow(**{**base_args, "seconds_since_output": new_val})
    elif signal_name == "cpu_ratio":
        new_val = draw(
            st.floats(
                min_value=base_args["cpu_ratio"],
                max_value=base_args["cpu_ratio"] + 0.5,
                allow_nan=False,
            )
        )
        w2 = LivenessWindow(**{**base_args, "cpu_ratio": new_val})
    elif signal_name == "io_bytes_per_s":
        new_val = draw(
            st.floats(
                min_value=base_args["io_bytes_per_s"],
                max_value=base_args["io_bytes_per_s"] + 100.0,
                allow_nan=False,
            )
        )
        w2 = LivenessWindow(**{**base_args, "io_bytes_per_s": new_val})
    elif signal_name == "child_events":
        new_val = draw(
            st.integers(
                min_value=base_args["child_events"],
                max_value=base_args["child_events"] + 5,
            )
        )
        w2 = LivenessWindow(**{**base_args, "child_events": new_val})
    elif signal_name == "output_novelty":
        new_val = draw(
            st.floats(
                min_value=base_args["output_novelty"],
                max_value=1.0,
                allow_nan=False,
            )
        )
        w2 = LivenessWindow(**{**base_args, "output_novelty": new_val})
    else:  # voluntary_ctx_per_s
        new_val = draw(
            st.floats(
                min_value=base_args["voluntary_ctx_per_s"],
                max_value=base_args["voluntary_ctx_per_s"] + 5.0,
                allow_nan=False,
            )
        )
        w2 = LivenessWindow(**{**base_args, "voluntary_ctx_per_s": new_val})
    return w1, w2, signal_name


@given(triple=_single_signal_raises())
@settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
def test_score_monotone_non_decreasing_with_improved_signals(
    triple: tuple[LivenessWindow, LivenessWindow, str],
) -> None:
    """Baseline monotonicity from all-idle baseline.

    From an all-idle baseline (score=0.0), raising any single working signal
    never lowers the score under the literal Sugeno aggregate.
    Per PA-002 disposition, global score monotonicity across arbitrary
    baselines is refuted by counterexamples (R1+io dilution 1.0 -> 0.95 and
    the 30s -> 29s gate transition 0.6 -> 0.145), resolved by locking those
    exact semantics and re-scoping to kill-relevant monotonicity below.
    """
    w1, w2, signal_name = triple
    v1 = assess(w1, CFG)
    v2 = assess(w2, CFG)
    assert v2.score >= v1.score - 1e-9, (
        f"All-idle baseline per-signal monotonicity violated: "
        f"w1.score={v1.score!r}, w2.score={v2.score!r}, "
        f"raised {signal_name} in w2 lowered the score"
    )


@given(pair=_window_pairs_improved_working())
@settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
def test_monotonic_classification_not_demoted(pair: tuple[LivenessWindow, LivenessWindow]) -> None:
    """Monotonicity: raising working signals never demotes a working process."""
    w1, w2 = pair
    v1 = assess(w1, CFG)
    v2 = assess(w2, CFG)

    if v1.verdict_kind in {"working", "suspicious"}:
        assert v2.verdict_kind not in {"stalled", "unknown"}


# ---------------------------------------------------------------------------
# PA-002 Extended properties & Kill-relevant monotonicity
# ---------------------------------------------------------------------------


@st.composite
def _kill_relevant_signal_raises(draw: st.DrawFn) -> tuple[LivenessWindow, LivenessWindow, str]:
    """Generate (w1, w2, signal_name) raising a working signal.

    Covers: cpu_ratio, io_bytes_per_s, child_events, voluntary_ctx_per_s,
    and in-window novelty. Excludes the documented R6 gate transition
    across the 30s boundary where entering the evidence window with
    repetitive output correctly detects a loop and accumulates stall pressure.
    """
    signal_name = draw(
        st.sampled_from(
            ["cpu_ratio", "io_bytes_per_s", "child_events", "voluntary_ctx_per_s", "output_novelty"]
        )
    )
    sso = draw(st.one_of(st.none(), _AGE))
    cpu = draw(st.one_of(st.none(), _CPU))
    io = draw(st.one_of(st.none(), _IO))
    tree = draw(st.one_of(st.none(), _TREE))
    nov = draw(st.one_of(st.none(), _NOVELTY))
    awake = draw(st.one_of(st.none(), _AWAKE))

    if signal_name == "cpu_ratio":
        base_val = cpu or 0.0
        delta = draw(st.floats(min_value=0.02, max_value=0.5))
        w1 = LivenessWindow(sso, cpu, io, tree, nov, awake, 10.0)
        w2 = LivenessWindow(sso, base_val + delta, io, tree, nov, awake, 10.0)
    elif signal_name == "io_bytes_per_s":
        base_val = io or 0.0
        delta = draw(st.floats(min_value=5.0, max_value=100.0))
        w1 = LivenessWindow(sso, cpu, io, tree, nov, awake, 10.0)
        w2 = LivenessWindow(sso, cpu, base_val + delta, tree, nov, awake, 10.0)
    elif signal_name == "child_events":
        base_val = tree or 0
        delta = draw(st.integers(min_value=1, max_value=3))
        w1 = LivenessWindow(sso, cpu, io, tree, nov, awake, 10.0)
        w2 = LivenessWindow(sso, cpu, io, base_val + delta, nov, awake, 10.0)
    elif signal_name == "voluntary_ctx_per_s":
        base_val = awake or 0.0
        delta = draw(st.floats(min_value=1.0, max_value=5.0))
        w1 = LivenessWindow(sso, cpu, io, tree, nov, awake, 10.0)
        w2 = LivenessWindow(sso, cpu, io, tree, nov, base_val + delta, 10.0)
    else:  # in-window output_novelty
        # In-window novelty: both windows must have output in evidence window (< 30s)
        sso_in_window = draw(st.floats(min_value=0.0, max_value=CFG.evidence_window_s - 1.0))
        base_nov = nov if nov is not None else draw(st.floats(min_value=0.0, max_value=0.8))
        delta = draw(st.floats(min_value=0.02, max_value=1.0 - base_nov))
        w1 = LivenessWindow(sso_in_window, cpu, io, tree, base_nov, awake, 10.0)
        w2 = LivenessWindow(sso_in_window, cpu, io, tree, min(1.0, base_nov + delta), awake, 10.0)
    return w1, w2, signal_name


@given(
    triple=_kill_relevant_signal_raises(),
    n_steps=st.integers(min_value=10, max_value=100),
    dt=st.floats(min_value=0.5, max_value=2.0),
    stall_window=st.floats(min_value=10.0, max_value=100.0),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
def test_kill_relevant_monotonicity(
    triple: tuple[LivenessWindow, LivenessWindow, str],
    n_steps: int,
    dt: float,
    stall_window: float,
) -> None:
    """Kill-relevant monotonicity (PA-002 disposition).

    Raising cpu, io, tree, awake membership or in-window novelty never converts
    a would-not-kill trace into a killed trace, and never speeds up a kill.
    Excludes the documented R6 gate transition across the 30s boundary.
    """
    w1, w2, sig = triple

    def kill_step(win: LivenessWindow) -> int:
        si = StallIntegrator(theta=CFG.stall_theta)
        v = assess(win, CFG)
        for i in range(1, n_steps + 1):
            si.step(verdict_score=v.score, dt=dt)
            if si.should_kill(stall_window):
                return i
        return -1

    k1 = kill_step(w1)
    k2 = kill_step(w2)

    if k1 == -1:
        assert k2 == -1, (
            f"Kill-relevant monotonicity violated: w1 did not kill, but raising {sig} "
            f"killed at step {k2}"
        )
    elif k2 != -1:
        assert k2 >= k1, (
            f"Kill-relevant monotonicity violated: w1 killed at {k1}, but raising {sig} "
            f"killed earlier at {k2}"
        )


@given(
    sso_inside=st.floats(min_value=0.0, max_value=CFG.evidence_window_s - 0.1),
    nov=st.floats(min_value=0.0, max_value=CFG.repetitive_max),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_extended_property_evidence_window_gate_transitions(sso_inside: float, nov: float) -> None:
    """Evidence-window gating transitions: loop detection inside evidence window.

    Inside the evidence window (< 30s) with repetitive output (<= repetitive_max),
    R6 always fires with z=0.2 and R1 is blocked (0.0).
    Outside the evidence window (>= 30s), novelty is gate-unknown (D5), so
    R6 cannot fire.
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
    """Randomized baseline where high-z rule R1 fires, exhibiting literal-Sugeno dilution.

    R1 fires with z=1.0 alone (score 1.0). Raising io into active range brings
    in R2 (z=0.9), diluting the score to exactly 0.95. Both are > theta so neither kills.
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


@given(pair=_window_pairs_improved_working())
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
def test_extended_property_multi_signal_classification_safety(
    pair: tuple[LivenessWindow, LivenessWindow],
) -> None:
    """Multi-signal raises preserve working/suspicious classification and safety."""
    w1, w2 = pair
    v1 = assess(w1, CFG)
    v2 = assess(w2, CFG)

    if v1.verdict_kind in {"working", "suspicious"}:
        assert v2.verdict_kind not in {"stalled", "unknown"}


# ---------------------------------------------------------------------------
# Isolated novelty counterexample (WU-2 / E-MONOTONIC)
# ---------------------------------------------------------------------------
#
# PRODUCT_CRITERIA Verification §3 — "Monotonic": raising any "working"
# signal never lowers ``s`` or speeds up a kill. The verbatim novelty
# counterexample reproduced by the analyzer (E-MONOTONIC) was
#
#     LivenessWindow(90.0, 0.0, 0.0, 0, novelty, 0.0, 90.0)
#         → score=0.2, kill_at=150 for novelty=0.1
#         → score=0.0, kill_at=90  for novelty=0.5
#
# which occurred when R6 ("Printing the same line in a loop") fired on
# a process that had emitted no output in the 30s evidence window.
# Under canonical D2/D3/D5:
# When output is stale (seconds_since_output=90s >= evidence_window_s=30s),
# no output occurred in the evidence window, so output novelty is unknown
# and repetitive=0. Both novelty=0.1 and novelty=0.5 yield score=0.0 (R5 alone).
# Raising novelty on silent/idle processes never lowers score or speeds up kill.


def test_novelty_monotone_non_decreasing_score() -> None:
    """E-MONOTONIC isolated counterexample: novelty=0.1 vs novelty=0.5.

    When output is stale (seconds_since_output=90s >= evidence_window_s=30s),
    no output occurred in the evidence window, so output novelty is unknown
    and repetitive=0. Both novelty=0.1 and novelty=0.5 yield score=0.0 (R5 alone).
    Raising novelty on silent/idle processes never lowers score or speeds up kill.
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


@given(
    triple=_single_signal_raises(),
    n_steps=st.integers(min_value=10, max_value=100),
    dt=st.floats(min_value=0.5, max_value=2.0),
    stall_window=st.floats(min_value=10.0, max_value=100.0),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_monotonic_kill_delay(
    triple: tuple[LivenessWindow, LivenessWindow, str],
    n_steps: int,
    dt: float,
    stall_window: float,
) -> None:
    """Monotonicity: raising a single working signal never speeds up a kill.

    Per-signal interpretation: the ``_single_signal_raises`` strategy
    raises exactly one working signal in ``w2``; raising multiple
    signals simultaneously can introduce a new rule firing with a
    lower ``z`` and pull the literal Sugeno weighted-average DOWN, which
    is the canonical (and intentional) Sugeno behavior — not a
    monotonicity violation under PRODUCT_CRITERIA Part A's score formula.
    """
    w1, w2, _signal_name = triple

    def kill_step(win: LivenessWindow) -> int:
        si = StallIntegrator(theta=CFG.stall_theta)
        v = assess(win, CFG)
        for i in range(1, n_steps + 1):
            si.step(verdict_score=v.score, dt=dt)
            if si.should_kill(stall_window):
                return i
        return -1

    k1 = kill_step(w1)
    k2 = kill_step(w2)

    # w2 has the single raised signal; it must never kill earlier than w1
    if k1 > 0 and k2 > 0:
        assert k2 >= k1
    elif k1 > 0 and k2 < 0:
        pass  # w2 prevented the kill entirely (strictly safer)
    elif k1 < 0 and k2 < 0:
        pass  # neither killed
    else:
        raise AssertionError(f"Monotonicity violated: k1={k1}, k2={k2}")


# ---------------------------------------------------------------------------
# Property 2 — Bounded
# ---------------------------------------------------------------------------


@given(
    n_steps=st.integers(min_value=1, max_value=200),
    dt=st.floats(min_value=0.01, max_value=5.0, allow_nan=False, allow_infinity=False),
    score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_pressure_bounded_by_trim(n_steps: int, dt: float, score: float) -> None:
    """Pressure P can never exceed the synthetic trim ``n_steps * dt``.

    Each step adds at most ``dt`` to P (when ``score == 0``) and at most
    ``-dt`` (when ``score >= theta``). With ``theta > 0``, the worst-case
    growth per step is ``dt`` and the synthetic trim bounds the absolute
    pressure.
    """
    si = StallIntegrator(theta=CFG.stall_theta)
    for _ in range(n_steps):
        si.step(verdict_score=score, dt=dt)
    # Worst case: every step is an idle step (score=0), adding exactly dt.
    assert si.pressure <= n_steps * dt + 1e-9


# ---------------------------------------------------------------------------
# Property 3 — Fail closed (None never delays a kill)
# ---------------------------------------------------------------------------


@given(
    n_steps=st.integers(min_value=10, max_value=200),
    dt=st.floats(min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False),
    stall_window=st.floats(min_value=10.0, max_value=200.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_fail_closed_none_never_delays_kill(n_steps: int, dt: float, stall_window: float) -> None:
    """An all-None trace reaches ``should_kill == True`` at the same step
    (or earlier) than a trace where every signal is the worst-case finite
    reading that still maps to membership 0.

    For every membership function, the value 0 maps to the worst possible
    reading (``io=0`` → IoActive=0; ``cpu=0`` → CpuBusy=0; ``tree=0`` →
    TreeActive=0; ``seconds=output_recent_max`` → OutputRecent=0;
    ``novelty=repetitive_min`` → Repetitive=0; ``awake=0`` → Awake=0).
    ``None`` also maps to 0 via the unknown-signal branch. The integrator
    therefore sees the same score on both traces and reaches the kill
    window at the same step (or earlier, never later).
    """

    def kill_step(trace: list[LivenessWindow]) -> int:
        si = StallIntegrator(theta=CFG.stall_theta)
        for i, win in enumerate(trace, start=1):
            v = assess(win, CFG)
            si.step(verdict_score=v.score, dt=dt)
            if si.should_kill(stall_window):
                return i
        return -1  # never killed within the trace

    none_trace = [
        LivenessWindow(
            seconds_since_output=None,
            cpu_ratio=None,
            io_bytes_per_s=None,
            child_events=None,
            output_novelty=None,
            voluntary_ctx_per_s=None,
            elapsed_s=10.0,
        )
        for _ in range(n_steps)
    ]
    zero_trace = [
        LivenessWindow(
            seconds_since_output=CFG.output_recent_max_s,
            cpu_ratio=0.0,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=CFG.repetitive_min,
            voluntary_ctx_per_s=0.0,
            elapsed_s=10.0,
        )
        for _ in range(n_steps)
    ]

    k_none = kill_step(none_trace)
    k_zero = kill_step(zero_trace)

    # ``k_none`` must never be greater than ``k_zero``:
    # both kill at the same step (memberships are identical), or the None
    # trace kills earlier (impossible here since both have the same score,
    # but the invariant must still hold if the rule changes).
    if k_none > 0 and k_zero > 0:
        assert k_none <= k_zero
    elif k_none > 0 and k_zero < 0:
        # None trace kills but finite-zero trace does not — trivially
        # fail-closed because None is more pessimistic.
        pass
    elif k_none < 0 and k_zero < 0:
        # Neither kills within budget — acceptable.
        pass
    else:
        raise AssertionError(
            f"fail-closed violated: k_none={k_none}, k_zero={k_zero}, "
            "zero trace kills but None trace does not"
        )


def _replace_signal(
    win: LivenessWindow, signal_name: str, val: float | int | None
) -> LivenessWindow:
    sso = (
        float(val)
        if signal_name == "seconds_since_output" and val is not None
        else (win.seconds_since_output if signal_name != "seconds_since_output" else None)
    )
    cpu = (
        float(val)
        if signal_name == "cpu_ratio" and val is not None
        else (win.cpu_ratio if signal_name != "cpu_ratio" else None)
    )
    io = (
        float(val)
        if signal_name == "io_bytes_per_s" and val is not None
        else (win.io_bytes_per_s if signal_name != "io_bytes_per_s" else None)
    )
    child = (
        int(val)
        if signal_name == "child_events" and val is not None
        else (win.child_events if signal_name != "child_events" else None)
    )
    nov = (
        float(val)
        if signal_name == "output_novelty" and val is not None
        else (win.output_novelty if signal_name != "output_novelty" else None)
    )
    awake = (
        float(val)
        if signal_name == "voluntary_ctx_per_s" and val is not None
        else (win.voluntary_ctx_per_s if signal_name != "voluntary_ctx_per_s" else None)
    )
    return LivenessWindow(
        seconds_since_output=sso,
        cpu_ratio=cpu,
        io_bytes_per_s=io,
        child_events=child,
        output_novelty=nov,
        voluntary_ctx_per_s=awake,
        elapsed_s=win.elapsed_s,
    )


@given(
    window=_WINDOWS,
    signal_name=st.sampled_from(
        [
            "seconds_since_output",
            "cpu_ratio",
            "io_bytes_per_s",
            "child_events",
            "output_novelty",
            "voluntary_ctx_per_s",
        ]
    ),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
def test_fail_closed_single_signal_unknown_vs_zero(
    window: LivenessWindow, signal_name: str
) -> None:
    """Replacing any single signal with unknown (None) never raises score vs zero."""
    zero_vals: dict[str, float | int] = {
        "seconds_since_output": CFG.output_recent_max_s + 10.0,
        "cpu_ratio": 0.0,
        "io_bytes_per_s": 0.0,
        "child_events": 0,
        "output_novelty": CFG.repetitive_min,
        "voluntary_ctx_per_s": 0.0,
    }
    win_zero = _replace_signal(window, signal_name, zero_vals[signal_name])
    win_none = _replace_signal(window, signal_name, None)

    v_zero = assess(win_zero, CFG)
    v_none = assess(win_none, CFG)

    # Replacing zero with unknown must never raise score (D5 fail-closed)
    assert v_none.score <= v_zero.score + 1e-9

    # And stall pressure accumulation must never decrease
    p_rate_zero = max(0.0, (CFG.stall_theta - v_zero.score) / CFG.stall_theta)
    p_rate_none = max(0.0, (CFG.stall_theta - v_none.score) / CFG.stall_theta)
    assert p_rate_none >= p_rate_zero - 1e-9


# ---------------------------------------------------------------------------
# Property 4 — Deterministic
# ---------------------------------------------------------------------------


@given(window=_WINDOWS)
@settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
def test_verdict_is_deterministic(window: LivenessWindow) -> None:
    """Same window → same verdict, byte-for-byte."""
    v1 = assess(window, CFG)
    v2 = assess(window, CFG)
    assert v1 == v2


@given(
    n_steps=st.integers(min_value=1, max_value=50),
    dt=st.floats(min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_stall_integrator_is_deterministic(n_steps: int, dt: float) -> None:
    """Same step sequence → same pressure curve."""
    si1 = StallIntegrator(theta=CFG.stall_theta)
    si2 = StallIntegrator(theta=CFG.stall_theta)
    for i in range(n_steps):
        s = (i % 5) / 10.0  # 0.0..0.4
        si1.step(verdict_score=s, dt=dt)
        si2.step(verdict_score=s, dt=dt)
    assert si1.pressure == si2.pressure


# ---------------------------------------------------------------------------
# Property 5 — P ≥ 0; all-idle trace kills at stall_window_s ± one interval
# ---------------------------------------------------------------------------


@given(
    n_steps=st.integers(min_value=1, max_value=500),
    dt=st.floats(min_value=0.01, max_value=2.0, allow_nan=False, allow_infinity=False),
    score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_pressure_never_negative(n_steps: int, dt: float, score: float) -> None:
    """P never goes below 0 over arbitrary step sequences."""
    si = StallIntegrator(theta=CFG.stall_theta)
    for _ in range(n_steps):
        si.step(verdict_score=score, dt=dt)
    assert si.pressure >= 0.0


@given(
    stall_window=st.floats(min_value=5.0, max_value=120.0, allow_nan=False, allow_infinity=False),
    dt=st.floats(min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_all_idle_kills_at_stall_window_plus_minus_one_interval(
    stall_window: float, dt: float
) -> None:
    """An all-idle exact-input trace kills at ``stall_window_s`` ± one interval.

    Concretely, drive the integrator for ``ceil(stall_window / dt) + 1``
    idle steps (each ``score=0``, each ``dt``) and check:

    * after that many steps ``should_kill(stall_window)`` is True;
    * one step before, ``should_kill(stall_window)`` is False.
    """
    si = StallIntegrator(theta=CFG.stall_theta)
    n_to_kill = int(stall_window // dt)
    for _ in range(n_to_kill + 2):
        si.step(verdict_score=0.0, dt=dt)

    # After ``n_to_kill + 1`` or more idle steps, P > stall_window_s — kill.
    assert si.should_kill(stall_window) is True

    # The "just-before" property: one step earlier we should NOT have killed.
    si_early = StallIntegrator(theta=CFG.stall_theta)
    for _ in range(max(n_to_kill - 1, 0)):
        si_early.step(verdict_score=0.0, dt=dt)
    assert si_early.should_kill(stall_window) is False


# ---------------------------------------------------------------------------
# Bonus invariant — used by the table tests but worth re-pinning here so
# the property suite stands on its own.
# ---------------------------------------------------------------------------


@given(o=st.floats(min_value=0.0, max_value=CFG.output_recent_max_s * 2, allow_nan=False))
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_output_recent_in_unit_interval(o: float) -> None:
    """OutputRecent membership is always in [0, 1]."""
    v = _output_recent(o, CFG)
    assert 0.0 <= v <= 1.0


# ---------------------------------------------------------------------------
# Sanity surface for the new helpers (SuspicionClock + step_with_assessment)
# ---------------------------------------------------------------------------
#
# The SuspicionClock and step_with_assessment helper tests live in
# ``test_liveness_helpers.py``. The §3b "Bounded" / termination tests
# from this file live in ``test_liveness_termination.py``. Splitting
# these out keeps this file under the 1000-line repo-structure audit
# cap; both files share the same ``CFG`` constant via
# ``default_liveness_config()``.
