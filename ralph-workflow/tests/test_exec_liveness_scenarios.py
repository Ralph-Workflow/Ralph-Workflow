from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from ralph import timeout_defaults as _td
from ralph.process.manager._liveness_assessor import (
    LivenessConfig,
    LivenessWindow,
    StallIntegrator,
    assess,
    default_liveness_config,
)

CFG: LivenessConfig = default_liveness_config()
_DEFAULT_STALL_WINDOW_S: float = _td.EXEC_DEFAULT_TIMEOUT_MS / 1000.0
_DEFAULT_DT: float = _td.EXEC_LIVENESS_SAMPLE_INTERVAL_S


@dataclass(frozen=True)
class SimulationResult:
    completed: bool
    killed: bool
    kill_cause: str
    kill_time_s: float
    suspicious_reported: bool
    first_suspicious_at_s: float | None
    final_pressure: float
    total_elapsed_s: float


def _run_scenario(
    sample_fn: Callable[[float], LivenessWindow],
    *,
    total_duration_s: float,
    dt: float = _DEFAULT_DT,
    stall_window_s: float = _DEFAULT_STALL_WINDOW_S,
    config: LivenessConfig = CFG,
) -> SimulationResult:
    integrator = StallIntegrator(theta=config.stall_theta)

    suspicious_streak_s = 0.0
    suspicious_reported = False
    first_suspicious_at: float | None = None
    elapsed = 0.0

    while elapsed < total_duration_s:
        elapsed += dt
        window = sample_fn(elapsed)
        verdict = assess(window, config)
        pressure = integrator.step(verdict.score, dt)

        if verdict.suspicious:
            suspicious_streak_s += dt
            if suspicious_streak_s >= _td.EXEC_SUSPICIOUS_REPORT_S:
                suspicious_reported = True
                if first_suspicious_at is None:
                    first_suspicious_at = elapsed
        else:
            suspicious_streak_s = 0.0

        if integrator.should_kill(stall_window_s):
            return SimulationResult(
                completed=False,
                killed=True,
                kill_cause="stalled",
                kill_time_s=elapsed,
                suspicious_reported=suspicious_reported,
                first_suspicious_at_s=first_suspicious_at,
                final_pressure=pressure,
                total_elapsed_s=elapsed,
            )

        if suspicious_streak_s >= _td.EXEC_NO_PROGRESS_WINDOW_S:
            return SimulationResult(
                completed=False,
                killed=True,
                kill_cause="no_progress",
                kill_time_s=elapsed,
                suspicious_reported=suspicious_reported,
                first_suspicious_at_s=first_suspicious_at,
                final_pressure=pressure,
                total_elapsed_s=elapsed,
            )

    return SimulationResult(
        completed=True,
        killed=False,
        kill_cause="",
        kill_time_s=0.0,
        suspicious_reported=suspicious_reported,
        first_suspicious_at_s=first_suspicious_at,
        final_pressure=integrator.pressure,
        total_elapsed_s=elapsed,
    )


def test_scenario_01_idle_sleep_stalled_at_window() -> None:
    stall_window_s = 90.0

    def sample(t: float) -> LivenessWindow:
        return LivenessWindow(
            seconds_since_output=t + 60.0,
            cpu_ratio=0.0,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=None,
            voluntary_ctx_per_s=0.0,
            elapsed_s=t,
        )

    res = _run_scenario(sample, total_duration_s=200.0, stall_window_s=stall_window_s)
    assert res.killed is True
    assert res.kill_cause == "stalled"
    assert abs(res.kill_time_s - stall_window_s) <= _DEFAULT_DT


def test_scenario_02_silent_compile_completes_suspicious_after_60s() -> None:
    compile_duration_s = 240.0

    def sample(t: float) -> LivenessWindow:
        return LivenessWindow(
            seconds_since_output=t + 60.0,
            cpu_ratio=0.8,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=None,
            voluntary_ctx_per_s=0.0,
            elapsed_s=t,
        )

    res = _run_scenario(sample, total_duration_s=compile_duration_s)
    assert res.completed is True
    assert res.killed is False
    assert res.suspicious_reported is True
    assert res.first_suspicious_at_s is not None
    assert abs(res.first_suspicious_at_s - _td.EXEC_SUSPICIOUS_REPORT_S) <= _DEFAULT_DT


def test_scenario_03_silent_cpu_spin_no_progress_at_15min() -> None:

    def sample(t: float) -> LivenessWindow:
        return LivenessWindow(
            seconds_since_output=t + 60.0,
            cpu_ratio=0.8,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=None,
            voluntary_ctx_per_s=0.0,
            elapsed_s=t,
        )

    res = _run_scenario(sample, total_duration_s=1200.0)
    assert res.killed is True
    assert res.kill_cause == "no_progress"
    assert res.suspicious_reported is True
    assert abs(res.kill_time_s - _td.EXEC_NO_PROGRESS_WINDOW_S) <= _DEFAULT_DT


def test_scenario_04_chatty_test_suite_completes() -> None:
    suite_duration_s = 1200.0

    def sample(t: float) -> LivenessWindow:
        return LivenessWindow(
            seconds_since_output=1.0,
            cpu_ratio=0.15,
            io_bytes_per_s=10.0,
            child_events=0,
            output_novelty=0.8,
            voluntary_ctx_per_s=1.0,
            elapsed_s=t,
        )

    res = _run_scenario(sample, total_duration_s=suite_duration_s)
    assert res.completed is True
    assert res.killed is False
    assert res.suspicious_reported is False
    assert res.final_pressure == 0.0


def test_scenario_05_child_fanout_quiet_parent_completes() -> None:
    suite_duration_s = 600.0

    def sample(t: float) -> LivenessWindow:
        return LivenessWindow(
            seconds_since_output=t + 60.0,
            cpu_ratio=0.05,
            io_bytes_per_s=2.0,
            child_events=2,
            output_novelty=None,
            voluntary_ctx_per_s=0.2,
            elapsed_s=t,
        )

    res = _run_scenario(sample, total_duration_s=suite_duration_s)
    assert res.completed is True
    assert res.killed is False
    assert res.final_pressure == 0.0


def test_scenario_06_dependency_install_io_heavy_completes() -> None:
    install_duration_s = 600.0

    def sample(t: float) -> LivenessWindow:
        return LivenessWindow(
            seconds_since_output=120.0,
            cpu_ratio=0.10,
            io_bytes_per_s=128.0,
            child_events=0,
            output_novelty=None,
            voluntary_ctx_per_s=1.0,
            elapsed_s=t,
        )

    res = _run_scenario(sample, total_duration_s=install_duration_s)
    assert res.completed is True
    assert res.killed is False
    assert res.final_pressure == 0.0


def test_scenario_07_repetitive_loop_stalled_via_r6() -> None:
    stall_window_s = 90.0

    def sample(t: float) -> LivenessWindow:
        return LivenessWindow(
            seconds_since_output=1.0,
            cpu_ratio=0.01,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=0.05,
            voluntary_ctx_per_s=0.0,
            elapsed_s=t,
        )

    expected_kill_time = stall_window_s / 0.6
    res = _run_scenario(sample, total_duration_s=300.0, stall_window_s=stall_window_s)
    assert res.killed is True
    assert res.kill_cause == "stalled"
    assert abs(res.kill_time_s - expected_kill_time) <= _DEFAULT_DT


def test_scenario_08_short_burst_then_idle_stalled_at_window_after_activity() -> None:
    burst_duration_s = 30.0
    stall_window_s = 90.0

    state: dict[str, float] = {"last_output_t": -1.0}

    def sample(t: float) -> LivenessWindow:
        if t <= burst_duration_s:
            state["last_output_t"] = t
            return LivenessWindow(
                seconds_since_output=0.0,
                cpu_ratio=0.2,
                io_bytes_per_s=10.0,
                child_events=0,
                output_novelty=1.0,
                voluntary_ctx_per_s=1.0,
                elapsed_s=t,
            )
        return LivenessWindow(
            seconds_since_output=max(0.0, t - state["last_output_t"]),
            cpu_ratio=0.0,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=None,
            voluntary_ctx_per_s=0.0,
            elapsed_s=t,
        )

    fine_res = _run_scenario(
        sample,
        total_duration_s=300.0,
        stall_window_s=stall_window_s,
        dt=0.01,
    )
    assert fine_res.killed is True
    assert fine_res.kill_cause == "stalled"
    expected_kill = fine_res.kill_time_s
    assert fine_res.kill_time_s == pytest.approx(167.5, abs=0.05)

    res = _run_scenario(sample, total_duration_s=300.0, stall_window_s=stall_window_s)
    assert res.killed is True
    assert res.kill_cause == "stalled"
    assert abs(res.kill_time_s - expected_kill) <= _DEFAULT_DT
    assert res.kill_time_s == 168.0

    proof1_floor_s = burst_duration_s + CFG.output_recent_min_s + stall_window_s
    assert fine_res.kill_time_s >= proof1_floor_s - 0.05, (
        f"PROOF 1 floor violated: kill at {fine_res.kill_time_s!r} is earlier "
        f"than last_output + EXEC_OUTPUT_RECENT_MAX_S + stall_window = {proof1_floor_s!r}"
    )
    criterion_deadline_s = burst_duration_s + stall_window_s + _DEFAULT_DT
    assert proof1_floor_s > criterion_deadline_s
    assert res.kill_time_s > criterion_deadline_s


def test_scenario_09_alternating_busy_idle_completes() -> None:
    total_duration_s = 600.0
    cycle_s = 40.0
    busy_s = 20.0

    def sample(t: float) -> LivenessWindow:
        in_cycle = t % cycle_s
        if in_cycle < busy_s:
            return LivenessWindow(
                seconds_since_output=0.0,
                cpu_ratio=0.5,
                io_bytes_per_s=10.0,
                child_events=0,
                output_novelty=1.0,
                voluntary_ctx_per_s=2.0,
                elapsed_s=t,
            )
        return LivenessWindow(
            seconds_since_output=90.0,
            cpu_ratio=0.0,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=None,
            voluntary_ctx_per_s=0.0,
            elapsed_s=t,
        )

    res = _run_scenario(sample, total_duration_s=total_duration_s)
    assert res.completed is True
    assert res.killed is False


def test_scenario_10_every_signal_except_output_unknown_matches_baseline() -> None:
    dt = 2.0
    total_duration_s = 200.0
    si_base = StallIntegrator(theta=CFG.stall_theta)
    si_unkn = StallIntegrator(theta=CFG.stall_theta)

    elapsed = 0.0
    while elapsed < total_duration_s:
        elapsed += dt
        o = elapsed % 120.0

        win_base = LivenessWindow(
            seconds_since_output=o,
            cpu_ratio=0.0,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=CFG.repetitive_min,
            voluntary_ctx_per_s=0.0,
            elapsed_s=elapsed,
        )
        win_unkn = LivenessWindow(
            seconds_since_output=o,
            cpu_ratio=None,
            io_bytes_per_s=None,
            child_events=None,
            output_novelty=None,
            voluntary_ctx_per_s=None,
            elapsed_s=elapsed,
        )

        v_base = assess(win_base, CFG)
        v_unkn = assess(win_unkn, CFG)
        p_base = si_base.step(v_base.score, dt)
        p_unkn = si_unkn.step(v_unkn.score, dt)

        assert v_unkn.score == pytest.approx(v_base.score)
        assert v_unkn.strongest_rule == v_base.strongest_rule
        assert v_unkn.verdict_kind == v_base.verdict_kind
        assert p_unkn == pytest.approx(p_base)


def test_scenario_11_45min_e2e_quiet_waits_completes() -> None:
    total_duration_s = 2700.0
    pulse_interval_s = 180.0

    def sample(t: float) -> LivenessWindow:
        in_pulse = (t % pulse_interval_s) < _DEFAULT_DT
        if in_pulse:
            return LivenessWindow(
                seconds_since_output=0.0,
                cpu_ratio=0.1,
                io_bytes_per_s=5.0,
                child_events=1,
                output_novelty=0.9,
                voluntary_ctx_per_s=20.0,
                elapsed_s=t,
            )
        return LivenessWindow(
            seconds_since_output=90.0,
            cpu_ratio=0.01,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=None,
            voluntary_ctx_per_s=20.0,
            elapsed_s=t,
        )

    res = _run_scenario(sample, total_duration_s=total_duration_s)
    assert res.completed is True
    assert res.killed is False
    assert res.final_pressure == 0.0


def test_scenario_12_poll_forever_harness_no_progress_at_15min() -> None:

    def sample(t: float) -> LivenessWindow:
        return LivenessWindow(
            seconds_since_output=t + 60.0,
            cpu_ratio=0.0,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=None,
            voluntary_ctx_per_s=20.0,
            elapsed_s=t,
        )

    res = _run_scenario(sample, total_duration_s=1200.0)
    assert res.killed is True
    assert res.kill_cause == "no_progress"
    assert res.suspicious_reported is True
    assert abs(res.kill_time_s - _td.EXEC_NO_PROGRESS_WINDOW_S) <= _DEFAULT_DT


def test_scenario_13_mid_run_deadlock_all_children_asleep_stalled() -> None:
    deadlock_at_s = 120.0
    stall_window_s = 90.0

    state: dict[str, float] = {"last_output_t": -1.0}

    def sample(t: float) -> LivenessWindow:
        if t <= deadlock_at_s:
            state["last_output_t"] = t
            return LivenessWindow(
                seconds_since_output=0.0,
                cpu_ratio=0.1,
                io_bytes_per_s=10.0,
                child_events=1,
                output_novelty=1.0,
                voluntary_ctx_per_s=20.0,
                elapsed_s=t,
            )
        return LivenessWindow(
            seconds_since_output=max(0.0, t - state["last_output_t"]),
            cpu_ratio=0.0,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=None,
            voluntary_ctx_per_s=0.0,
            elapsed_s=t,
        )

    fine_res = _run_scenario(
        sample,
        total_duration_s=400.0,
        stall_window_s=stall_window_s,
        dt=0.01,
    )
    assert fine_res.killed is True
    assert fine_res.kill_cause == "stalled"
    expected_kill = fine_res.kill_time_s
    assert fine_res.kill_time_s == pytest.approx(257.5, abs=0.05)

    res = _run_scenario(sample, total_duration_s=400.0, stall_window_s=stall_window_s)
    assert res.killed is True
    assert res.kill_cause == "stalled"
    assert abs(res.kill_time_s - expected_kill) <= _DEFAULT_DT
    assert res.kill_time_s == 258.0

    proof1_floor_s = deadlock_at_s + CFG.output_recent_min_s + stall_window_s
    assert fine_res.kill_time_s >= proof1_floor_s - 0.05, (
        f"PROOF 1 floor violated: kill at {fine_res.kill_time_s!r} is earlier "
        f"than last_output + EXEC_OUTPUT_RECENT_MAX_S + stall_window = {proof1_floor_s!r}"
    )
    criterion_deadline_s = deadlock_at_s + stall_window_s + _DEFAULT_DT
    assert proof1_floor_s > criterion_deadline_s
    assert res.kill_time_s > criterion_deadline_s


def test_scenario_14_never_output_shape_stalled_at_stall_window() -> None:
    stall_window_s = 90.0

    def sample(t: float) -> LivenessWindow:
        return LivenessWindow(
            seconds_since_output=None,
            cpu_ratio=0.0,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=None,
            voluntary_ctx_per_s=0.0,
            elapsed_s=t,
        )

    first = assess(sample(_DEFAULT_DT), CFG)
    assert first.score == 0.0
    assert first.strongest_rule == "R5"
    assert first.verdict_kind == "stalled"
    assert first.suspicious is False

    res = _run_scenario(sample, total_duration_s=200.0, stall_window_s=stall_window_s)
    assert res.killed is True
    assert res.kill_cause == "stalled"
    assert abs(res.kill_time_s - stall_window_s) <= _DEFAULT_DT
    assert res.kill_time_s == stall_window_s
    assert res.suspicious_reported is False
