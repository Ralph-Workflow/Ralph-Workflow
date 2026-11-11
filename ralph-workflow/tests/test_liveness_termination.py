from __future__ import annotations

import math
from collections.abc import Callable
from typing import TypedDict

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ralph import timeout_defaults as _td
from ralph.process.manager._liveness_assessor import (
    LivenessConfig,
    LivenessWindow,
    StallIntegrator,
    SuspicionClock,
    assess,
    default_liveness_config,
)

pytestmark = pytest.mark.timeout_seconds(30)

CFG: LivenessConfig = default_liveness_config()

_TERMINATION_DT: float = 2.0
_DEFAULT_STALL_WINDOW_S: float = _td.EXEC_DEFAULT_TIMEOUT_MS / 1000.0
_MAX_CEILING_S: float = min(_td.EXEC_JOB_CEILING_MAX_S, 4 * 3600.0)


class _SimulateTraceResult(TypedDict):
    terminated_by: str
    elapsed_s: float
    pressure_at_termination: float
    kill_step_s: float


def _simulate_trace(
    *,
    sample_fn: Callable[[float], LivenessWindow],
    ceiling_s: float,
    stall_window_s: float = _DEFAULT_STALL_WINDOW_S,
    suspicion_threshold_s: float = _td.EXEC_SUSPICIOUS_REPORT_S,
    no_progress_window_s: float = _td.EXEC_NO_PROGRESS_WINDOW_S,
    dt: float = _TERMINATION_DT,
) -> _SimulateTraceResult:
    si = StallIntegrator(theta=CFG.stall_theta)
    sc = SuspicionClock(threshold_s=suspicion_threshold_s)
    elapsed = 0.0
    while True:
        elapsed += dt
        win = sample_fn(elapsed)
        verdict = assess(win, CFG)
        si.step(verdict.score, dt)
        sc.step(strongest_rule=verdict.strongest_rule, dt=dt)

        if si.should_kill(stall_window_s):
            return {
                "terminated_by": "stalled",
                "elapsed_s": elapsed,
                "pressure_at_termination": si.pressure,
                "kill_step_s": stall_window_s,
            }
        if sc.streak_s >= no_progress_window_s:
            return {
                "terminated_by": "no_progress",
                "elapsed_s": elapsed,
                "pressure_at_termination": si.pressure,
                "kill_step_s": no_progress_window_s,
            }
        if elapsed >= ceiling_s:
            return {
                "terminated_by": "completed",
                "elapsed_s": elapsed,
                "pressure_at_termination": si.pressure,
                "kill_step_s": ceiling_s,
            }


@st.composite
def _random_signal_intensity(draw: st.DrawFn) -> int:
    return draw(st.integers(min_value=0, max_value=4))


def _sample_for_intensity(intensity: int, t: float) -> LivenessWindow:
    if intensity == 0:
        return LivenessWindow(
            seconds_since_output=t + 90.0,
            cpu_ratio=0.0,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=None,
            voluntary_ctx_per_s=0.0,
            elapsed_s=t,
        )
    if intensity == 1:
        return LivenessWindow(
            seconds_since_output=1.0,
            cpu_ratio=0.01,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=0.05,
            voluntary_ctx_per_s=0.0,
            elapsed_s=t,
        )
    if intensity == 2:
        return LivenessWindow(
            seconds_since_output=t + 60.0,
            cpu_ratio=0.05,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=None,
            voluntary_ctx_per_s=0.0,
            elapsed_s=t,
        )
    if intensity == 3:
        return LivenessWindow(
            seconds_since_output=0.0,
            cpu_ratio=0.1,
            io_bytes_per_s=120.0,
            child_events=1,
            output_novelty=1.0,
            voluntary_ctx_per_s=1.0,
            elapsed_s=t,
        )
    return LivenessWindow(
        seconds_since_output=0.0 if (int(t // 20.0) % 2 == 0) else 90.0,
        cpu_ratio=0.5 if (int(t // 20.0) % 2 == 0) else 0.0,
        io_bytes_per_s=10.0 if (int(t // 20.0) % 2 == 0) else 0.0,
        child_events=0,
        output_novelty=1.0 if (int(t // 20.0) % 2 == 0) else None,
        voluntary_ctx_per_s=2.0 if (int(t // 20.0) % 2 == 0) else 0.0,
        elapsed_s=t,
    )


@given(
    intensity_seq=st.lists(_random_signal_intensity(), min_size=1, max_size=12),
    ceiling_s=st.floats(
        min_value=_td.EXEC_JOB_CEILING_S,
        max_value=_td.EXEC_JOB_CEILING_MAX_S,
        allow_nan=False,
        allow_infinity=False,
    ),
)
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_randomized_traces_always_terminate_by_ceiling(
    intensity_seq: list[int], ceiling_s: float
) -> None:
    ceiling_round = float(int(ceiling_s))
    cycle = tuple(intensity_seq)
    state = {"tick": 0}

    def sample_fn(t: float) -> LivenessWindow:
        idx = state["tick"] % len(cycle)
        state["tick"] += 1
        return _sample_for_intensity(cycle[idx], t)

    result = _simulate_trace(sample_fn=sample_fn, ceiling_s=ceiling_round)
    assert result["terminated_by"] in {"stalled", "no_progress", "completed"}
    assert result["elapsed_s"] is not None
    assert result["elapsed_s"] <= ceiling_round + _TERMINATION_DT + 1e-9


def test_randomized_termination_by_ceiling_default_1h_completes_or_kills() -> None:
    rng_seed = 0xC0FFEE
    import random

    rng = random.Random(rng_seed)
    intensity_seq = [rng.randint(0, 4) for _ in range(9)]
    cycle = tuple(intensity_seq)
    state = {"tick": 0}

    def sample_fn(t: float) -> LivenessWindow:
        idx = state["tick"] % len(cycle)
        state["tick"] += 1
        return _sample_for_intensity(cycle[idx], t)

    result = _simulate_trace(sample_fn=sample_fn, ceiling_s=_td.EXEC_JOB_CEILING_S)
    assert result["terminated_by"] in {"stalled", "no_progress", "completed"}
    assert result["elapsed_s"] is not None
    assert result["elapsed_s"] <= _td.EXEC_JOB_CEILING_S + _TERMINATION_DT + 1e-9
    assert result["pressure_at_termination"] is not None
    assert result["pressure_at_termination"] >= 0.0


def test_pure_3h_run_under_4h_ceiling_completes() -> None:
    compile_duration_s = 3 * 3600.0
    ceiling_s = _td.EXEC_JOB_CEILING_MAX_S
    max_steps = math.ceil(compile_duration_s / _TERMINATION_DT)

    def sample_fn(t: float) -> LivenessWindow:
        return LivenessWindow(
            seconds_since_output=t + 90.0,
            cpu_ratio=0.8,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=None,
            voluntary_ctx_per_s=0.0,
            elapsed_s=t,
        )

    si = StallIntegrator(theta=CFG.stall_theta)
    sc = SuspicionClock()
    suspicion_break = False
    tick = 0
    finished_at = 0.0
    while tick < max_steps:
        tick += 1
        elapsed = tick * _TERMINATION_DT
        win = sample_fn(elapsed)
        verdict = assess(win, CFG)
        si.step(verdict.score, _TERMINATION_DT)
        sc.step(strongest_rule=verdict.strongest_rule, dt=_TERMINATION_DT)
        if si.should_kill(_DEFAULT_STALL_WINDOW_S):
            raise AssertionError(f"Stall window fired at t={elapsed}s for a 0.6-score trace")
        finished_at = elapsed

    assert finished_at >= compile_duration_s - _TERMINATION_DT
    assert finished_at <= ceiling_s
    assert si.pressure == pytest.approx(0.0, abs=1e-9)
    if sc.streak_s >= _td.EXEC_NO_PROGRESS_WINDOW_S:
        suspicion_break = True
    assert suspicion_break is True
    assert sc.streak_s >= _td.EXEC_SUSPICIOUS_REPORT_S
