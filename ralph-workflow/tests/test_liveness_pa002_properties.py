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

pytestmark = pytest.mark.timeout_seconds(30)

CFG: LivenessConfig = default_liveness_config()


@st.composite
def _single_signal_raises_in_changing_trace(
    draw: st.DrawFn,
) -> tuple[LivenessWindow, LivenessWindow, str]:
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
    else:
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
    w1, w2, signal_name = triple

    v1 = assess(w1, CFG)
    v2 = assess(w2, CFG)

    assert 0.0 <= v2.score <= 1.0 + 1e-9, (
        f"Score out of unit interval after raising {signal_name}: {v2.score!r}"
    )

    if v1.verdict_kind in {"working", "suspicious"}:
        assert v2.verdict_kind in {"working", "suspicious"}, (
            f"Classification regressed after raising {signal_name}: "
            f"v1={v1.verdict_kind!r} -> v2={v2.verdict_kind!r}"
        )

    si = StallIntegrator(theta=CFG.stall_theta)
    for _ in range(5):
        si.step(verdict_score=0.0, dt=1.0)
    si.step(verdict_score=v2.score, dt=1.0)
    for _ in range(5):
        si.step(verdict_score=0.0, dt=1.0)
    assert si.pressure >= 0.0, (
        f"Pressure drifted negative after raise-then-idle trace: {si.pressure!r}"
    )

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


def _run_changing_io_kill_trace(
    io_during_working: float,
    pre_idle_s: int = 80,
    working_s: int = 20,
    stall_window_s: float = 90.0,
    dt: float = 1.0,
) -> tuple[int, float]:
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
    k0, p0 = _run_changing_io_kill_trace(io_during_working=0.0)
    k64, p64 = _run_changing_io_kill_trace(io_during_working=64.0)
    k_big, p_big = _run_changing_io_kill_trace(io_during_working=65536.0)

    assert k0 == 130, f"case-(c) io=0 baseline shifted: got kill_time={k0}, expected 130"
    assert k64 == 129, f"case-(c) io=64 shifted: got kill_time={k64}, expected 129"
    assert k_big == 129, f"case-(c) io=65536 shifted: got kill_time={k_big}, expected 129"

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


def test_novelty_monotone_on_silent_idle_processes() -> None:
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

    assert v_high.score >= v_low.score - 1e-9, (
        f"novelty monotonicity violated: novelty=0.1 score={v_low.score!r} "
        f"vs novelty=0.5 score={v_high.score!r}; raising novelty lowered "
        f"the score (E-MONOTONIC counterexample)"
    )

    p_rate_low = max(0.0, (CFG.stall_theta - v_low.score) / CFG.stall_theta)
    p_rate_high = max(0.0, (CFG.stall_theta - v_high.score) / CFG.stall_theta)
    assert p_rate_high <= p_rate_low + 1e-9, (
        f"novelty monotonicity violated at the pressure-rate layer: "
        f"p_rate_low={p_rate_low!r}, p_rate_high={p_rate_high!r}; raising "
        f"novelty accelerated stall-pressure accumulation"
    )

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

    expected_kills = stall_window_s / dt
    assert abs(k_low - expected_kills) <= 1, (
        f"novelty=0.1 should kill at {expected_kills} ticks (±1); got {k_low}"
    )
    assert abs(k_high - expected_kills) <= 1, (
        f"novelty=0.5 should kill at {expected_kills} ticks (±1); got {k_high}"
    )


def test_novelty_monotone_when_output_recent() -> None:
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


def _run_burst_then_idle_offset(
    config: LivenessConfig,
    *,
    burst_duration_s: float,
    stall_window_s: float,
    dt: float,
) -> tuple[float, float]:
    si = StallIntegrator(theta=config.stall_theta)
    t = 0.0
    last_output_s = 0.0
    while t < burst_duration_s:
        t += dt
        last_output_s = t
        si.step(
            verdict_score=assess(LivenessWindow(0.0, 0.2, 10.0, 0, 1.0, 1.0, t), config).score,
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
    dt = 0.1
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
        assert floor_offset_s > stall_window_s + dt


def test_proof1_floor_holds_for_every_theta() -> None:
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
    collapsed = dataclasses.replace(CFG, output_recent_min_s=0.0, output_recent_max_s=2.0)
    dt = _td.EXEC_LIVENESS_SAMPLE_INTERVAL_S
    horizon_s = 1200.0

    kill_time_s, last_output_s = _run_burst_then_idle_offset(
        collapsed, burst_duration_s=30.0, stall_window_s=90.0, dt=dt
    )
    assert kill_time_s - last_output_s <= 90.0 + dt

    def _chatty_survival(config: LivenessConfig) -> float | None:
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

    killed_at_s = _chatty_survival(collapsed)
    assert killed_at_s is not None, (
        "the collapsed breakpoints no longer kill the chatty suite; the "
        "trade-off that refuses this candidate must be re-measured"
    )
    assert 90.0 < killed_at_s < horizon_s

    assert _chatty_survival(CFG) is None, (
        "the shipped OutputRecent breakpoints must let a suite printing one "
        "line every 5 s survive 20 minutes (criterion 2)"
    )
