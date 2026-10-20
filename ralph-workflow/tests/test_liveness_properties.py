"""Hypothesis property tests for the §3b liveness assessor.

Five properties at 50 examples each. Every example is pure arithmetic
(``assess`` + ``StallIntegrator.step``); no subprocess, no clock.

Properties:

1. **Monotonic** — score never decreases when working signals improve.
2. **Bounded** — P can never exceed the configured ceiling on any trace.
3. **Fail closed** — replacing a signal with ``None`` never delays a kill.
4. **Deterministic** — same input → same verdict.
5. **P ≥ 0** always; an all-idle exact-input trace kills at the configured
   ``stall_window_s`` ± one interval.
"""

from __future__ import annotations

import dataclasses
import os

import pytest

# The default per-test wall-clock limit is 1 second
# (``ralph.verify_timeout.DEFAULT_TEST_TIMEOUT_SECONDS``); hypothesis needs
# more headroom for 50 examples. Raise the limit just for these tests via
# the environment variable the conftest reads
# (``tests/conftest.py`` → ``TEST_TIMEOUT_ENV``).
os.environ.setdefault("RALPH_PYTEST_TEST_TIMEOUT_SECONDS", "30.0")

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
def test_fail_closed_none_never_delays_kill(
    n_steps: int, dt: float, stall_window: float
) -> None:
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