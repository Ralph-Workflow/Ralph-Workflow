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

pytestmark = pytest.mark.timeout_seconds(30)

CFG: LivenessConfig = default_liveness_config()

_AGE = st.floats(min_value=0.0, max_value=CFG.output_recent_max_s + 30.0)
_CPU = st.floats(min_value=0.0, max_value=CFG.cpu_busy_max + 0.3)
_IO = st.floats(min_value=0.0, max_value=CFG.io_kbps_active + 100.0)
_TREE = st.integers(min_value=0, max_value=5)
_NOVELTY = st.floats(min_value=0.0, max_value=1.0)
_AWAKE = st.floats(min_value=0.0, max_value=CFG.awake_per_s_active + 5.0)

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


@st.composite
def _window_pairs_improved_working(draw: st.DrawFn) -> tuple[LivenessWindow, LivenessWindow]:
    w1_sso = draw(_AGE)
    w1_cpu = draw(_CPU)
    w1_io = draw(_IO)
    w1_tree = draw(_TREE)
    w1_nov = draw(_NOVELTY)
    w1_awake = draw(_AWAKE)

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
    assert assess(window, CFG).score >= 0.0


@given(window=_WINDOWS)
@settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
def test_score_bounded_by_unit_interval(window: LivenessWindow) -> None:
    v = assess(window, CFG)
    assert 0.0 <= v.score <= 1.0


@given(pair=_window_pairs_improved_working())
@settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
def test_classification_not_demoted_by_signal_raises(
    pair: tuple[LivenessWindow, LivenessWindow],
) -> None:
    w1, w2 = pair
    v1 = assess(w1, CFG)
    v2 = assess(w2, CFG)

    if v1.verdict_kind in {"working", "suspicious"}:
        assert v2.verdict_kind not in {"stalled", "unknown"}


@given(
    n_steps=st.integers(min_value=1, max_value=200),
    dt=st.floats(min_value=0.01, max_value=5.0, allow_nan=False, allow_infinity=False),
    score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_pressure_bounded_by_trim(n_steps: int, dt: float, score: float) -> None:
    si = StallIntegrator(theta=CFG.stall_theta)
    for _ in range(n_steps):
        si.step(verdict_score=score, dt=dt)
    assert si.pressure <= n_steps * dt + 1e-9


@given(
    n_steps=st.integers(min_value=10, max_value=200),
    dt=st.floats(min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False),
    stall_window=st.floats(min_value=10.0, max_value=200.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_fail_closed_none_never_delays_kill(n_steps: int, dt: float, stall_window: float) -> None:

    def kill_step(trace: list[LivenessWindow]) -> int:
        si = StallIntegrator(theta=CFG.stall_theta)
        for i, win in enumerate(trace, start=1):
            v = assess(win, CFG)
            si.step(verdict_score=v.score, dt=dt)
            if si.should_kill(stall_window):
                return i
        return -1

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

    if k_none > 0 and k_zero > 0:
        assert k_none <= k_zero
    elif (k_none > 0 and k_zero < 0) or (k_none < 0 and k_zero < 0):
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

    assert v_none.score <= v_zero.score + 1e-9

    p_rate_zero = max(0.0, (CFG.stall_theta - v_zero.score) / CFG.stall_theta)
    p_rate_none = max(0.0, (CFG.stall_theta - v_none.score) / CFG.stall_theta)
    assert p_rate_none >= p_rate_zero - 1e-9


@given(window=_WINDOWS)
@settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
def test_verdict_is_deterministic(window: LivenessWindow) -> None:
    v1 = assess(window, CFG)
    v2 = assess(window, CFG)
    assert v1 == v2


@given(
    n_steps=st.integers(min_value=1, max_value=50),
    dt=st.floats(min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_stall_integrator_is_deterministic(n_steps: int, dt: float) -> None:
    si1 = StallIntegrator(theta=CFG.stall_theta)
    si2 = StallIntegrator(theta=CFG.stall_theta)
    for i in range(n_steps):
        s = (i % 5) / 10.0
        si1.step(verdict_score=s, dt=dt)
        si2.step(verdict_score=s, dt=dt)
    assert si1.pressure == si2.pressure


@given(
    n_steps=st.integers(min_value=1, max_value=500),
    dt=st.floats(min_value=0.01, max_value=2.0, allow_nan=False, allow_infinity=False),
    score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_pressure_never_negative(n_steps: int, dt: float, score: float) -> None:
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
    si = StallIntegrator(theta=CFG.stall_theta)
    n_to_kill = int(stall_window // dt)
    for _ in range(n_to_kill + 2):
        si.step(verdict_score=0.0, dt=dt)

    assert si.should_kill(stall_window) is True

    si_early = StallIntegrator(theta=CFG.stall_theta)
    for _ in range(max(n_to_kill - 1, 0)):
        si_early.step(verdict_score=0.0, dt=dt)
    assert si_early.should_kill(stall_window) is False


@given(o=st.floats(min_value=0.0, max_value=CFG.output_recent_max_s * 2, allow_nan=False))
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_output_recent_in_unit_interval(o: float) -> None:
    v = _output_recent(o, CFG)
    assert 0.0 <= v <= 1.0
