"""Hypothesis property tests for the §3b liveness assessor.

Property families at 50 examples each. Every example is pure arithmetic
(``assess`` + ``StallIntegrator.step``); no subprocess, no clock.

Properties (PA-002 disposition, post-wt-19):

1. **Classification safety** — raising working signals never demotes a
   process from working/suspicious to stalled/unknown (this is a
   classification-bound property, NOT a score/kill monotonicity claim).
2. **Exact-semantics regressions & transitions** — the locked
   counterexample scores (R1+io dilution 1.0 -> 0.95, the
   evidence-window gate transition 0.6 -> 0.2/1.38, the R7->R4
   negated-antecedent boundary 0.55 -> 0.575, and the kill-timing
   non-monotonicity over a CHANGING trace where raising only the I/O
   rate during the middle working segment makes the kill arrive
   strictly EARLIER) live in ``test_liveness_pa002_properties.py``.
3. **Bounded** — P can never exceed the configured ceiling on any trace.
4. **Fail closed** — None / unknown signals never delay a kill.
5. **Deterministic** — same input → same verdict.
6. **P ≥ 0** always; an all-idle exact-input trace kills at the configured
   ``stall_window_s`` ± one interval.
7. **Bounded-from-baseline classification** — raising any single working
   signal from an all-idle baseline does not lower the classification
   (working/suspicious only). This is the only surviving baseline-relative
   safety property; score and kill-time monotonicity do NOT hold.

The "score / kill-time per-signal monotone" properties previously asserted
here (test_score_monotone_non_decreasing_with_improved_signals,
test_kill_relevant_monotonicity and test_monotonic_kill_delay) were
removed in the PA-002 / wt-19 disposition. The literal Sugeno aggregate
``s = Σwᵢzᵢ / Σwᵢ`` admits dilution: a new or raised rule can DILUTE an
already-firing high-z rule, so ``s`` is not globally or per-signal
monotone and a kill can therefore arrive slightly earlier when a working
signal is raised during a changing trace. The three counterexamples
(a) R1+io dilution (b) 30s -> 29s gate transition (c) kill-time earlier
under middle-segment I/O are locked as exact-semantics regressions in
``test_liveness_pa002_properties.py``; see the module docstring of
``ralph.process.manager._liveness_assessor`` for the full disposition
and the live ``tests/test_liveness_assessor.py`` regressions for the
isolated scores.

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


# REMOVED in PA-002 / wt-19:
#   - test_score_monotone_non_decreasing_with_improved_signals
#     (asserted per-signal score monotonicity, refuted by R1+io 1.0 -> 0.95
#      and the 30s -> 29s gate transition 0.6 -> 0.145)
#   - test_kill_relevant_monotonicity
#     (asserted kill-monotonicity over a SINGLE-WINDOW trace, refuted by
#      case (c) kill-time non-monotonicity over a CHANGING trace; the
#      helper exercised exactly one window repeated, so it could not
#      observe a changing-trace failure)
#   - test_monotonic_kill_delay
#     (asserted per-signal kill-monotone from constant-window trace;
#      same failure mode as the per-signal score property)


@given(pair=_window_pairs_improved_working())
@settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
def test_classification_not_demoted_by_signal_raises(
    pair: tuple[LivenessWindow, LivenessWindow],
) -> None:
    """Classification safety: raising working signals never demotes
    a working/suspicious process to stalled/unknown.

    This is a CLASSIFICATION-bound property, NOT a score/kill
    monotonicity claim. Raising any combination of working signals can
    lower ``s`` (literal-Sugeno dilution) and can even accelerate a
    future kill on a *changing* trace (case (c) below), but the
    classification verdict_kind must never regress from
    working/suspicious to stalled/unknown. Asserts the only monotone
    property that actually holds under PA-002 disposition.
    """
    w1, w2 = pair
    v1 = assess(w1, CFG)
    v2 = assess(w2, CFG)

    if v1.verdict_kind in {"working", "suspicious"}:
        assert v2.verdict_kind not in {"stalled", "unknown"}


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
# from this file live in ``test_liveness_termination.py``. The PA-002
# exact-semantics / transition properties live in
# ``test_liveness_pa002_properties.py``. Splitting these out keeps this
# file under the 1000-line repo-structure audit cap; every split module
# builds the same ``CFG`` constant via ``default_liveness_config()``.
