"""Scenario trace library for the §3b pure deterministic liveness assessor.

13 pure signal/pressure traces evaluated directly against ``assess`` and
``StallIntegrator`` with scripted sample data and a synthetic time sequence.
All tests are pure arithmetic: no subprocess, no sleep, no wall-clock I/O.
Kill times are asserted accurate to one sample interval (± dt).
"""

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
    kill_cause: str  # "stalled", "no_progress", or ""
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
    """Step through synthetic samples until completion, stall-kill, or no-progress kill."""
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

        # Suspicion clock (D4): continuous time suspicious without break
        if verdict.suspicious:
            suspicious_streak_s += dt
            if suspicious_streak_s >= _td.EXEC_SUSPICIOUS_REPORT_S:
                suspicious_reported = True
                if first_suspicious_at is None:
                    first_suspicious_at = elapsed
        else:
            suspicious_streak_s = 0.0

        # Stall kill check (D3)
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

        # No-progress kill check (D4: continuous suspicion >= 15 min)
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


# ---------------------------------------------------------------------------
# Scenario 1 — Idle sleep
# ---------------------------------------------------------------------------


def test_scenario_01_idle_sleep_stalled_at_window() -> None:
    """Scenario 1: idle sleep far past window -> stalled at stall_window ± dt."""
    stall_window_s = 90.0

    def sample(t: float) -> LivenessWindow:
        # Output is already far past the window (> 60s)
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


# ---------------------------------------------------------------------------
# Scenario 2 — Silent compile (4 min, completes, suspicious after 60 s)
# ---------------------------------------------------------------------------


def test_scenario_02_silent_compile_completes_suspicious_after_60s() -> None:
    """Scenario 2: silent compile: CPU 0.8, no output, 4 min -> completes, suspicious reported."""
    compile_duration_s = 240.0  # 4 minutes

    def sample(t: float) -> LivenessWindow:
        # Output stale (> 60s), CPU busy (0.8)
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


# ---------------------------------------------------------------------------
# Scenario 3 — Silent CPU spin forever (no_progress at 15 min)
# ---------------------------------------------------------------------------


def test_scenario_03_silent_cpu_spin_no_progress_at_15min() -> None:
    """Scenario 3: silent CPU spin forever -> no_progress at 15 min (suspicious from 60 s)."""

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


# ---------------------------------------------------------------------------
# Scenario 4 — Chatty test suite, 20 min -> completes
# ---------------------------------------------------------------------------


def test_scenario_04_chatty_test_suite_completes() -> None:
    """Scenario 4: chatty test suite, 20 min -> completes."""
    suite_duration_s = 1200.0  # 20 minutes

    def sample(t: float) -> LivenessWindow:
        # Output every 1s, high novelty
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


# ---------------------------------------------------------------------------
# Scenario 5 — Test runner fanning out children, quiet parent -> completes
# ---------------------------------------------------------------------------


def test_scenario_05_child_fanout_quiet_parent_completes() -> None:
    """Scenario 5: test runner fanning out children, quiet parent -> completes."""
    suite_duration_s = 600.0  # 10 minutes

    def sample(t: float) -> LivenessWindow:
        # Quiet parent (> 60s), but children spawning/reaping
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


# ---------------------------------------------------------------------------
# Scenario 6 — Dependency install: I/O heavy, sparse output -> completes
# ---------------------------------------------------------------------------


def test_scenario_06_dependency_install_io_heavy_completes() -> None:
    """Scenario 6: dependency install: I/O heavy, sparse output -> completes."""
    install_duration_s = 600.0  # 10 minutes

    def sample(t: float) -> LivenessWindow:
        # Output sparse (e.g. 120s gaps), but disk I/O heavy (128 KiB/s >= 64 active)
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


# ---------------------------------------------------------------------------
# Scenario 7 — Repetitive same-line loop -> stalled via R6
# ---------------------------------------------------------------------------


def test_scenario_07_repetitive_loop_stalled_via_r6() -> None:
    """Scenario 7: repetitive same-line loop -> stalled via R6 (pulls score below theta)."""
    stall_window_s = 90.0

    def sample(t: float) -> LivenessWindow:
        # Printing same retry line every 1s (novelty <= 0.1), no I/O
        return LivenessWindow(
            seconds_since_output=1.0,
            cpu_ratio=0.01,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=0.05,
            voluntary_ctx_per_s=0.0,
            elapsed_s=t,
        )

    # R6 score = 0.2; delta rate = (0.5 - 0.2) / 0.5 = 0.6 per second
    # Expected kill at 90.0 / 0.6 = 150.0s ± dt
    expected_kill_time = stall_window_s / 0.6
    res = _run_scenario(sample, total_duration_s=300.0, stall_window_s=stall_window_s)
    assert res.killed is True
    assert res.kill_cause == "stalled"
    assert abs(res.kill_time_s - expected_kill_time) <= _DEFAULT_DT


# ---------------------------------------------------------------------------
# Scenario 8 — Short burst of activity then idle -> stalled at stall_window
# ---------------------------------------------------------------------------


def test_scenario_08_short_burst_then_idle_stalled_at_window_after_activity() -> None:
    """Scenario 8: short burst then idle -> stalled at stall_window after activity stops."""
    burst_duration_s = 30.0
    stall_window_s = 90.0

    def sample(t: float) -> LivenessWindow:
        if t <= burst_duration_s:
            # Active work: fresh output
            return LivenessWindow(
                seconds_since_output=0.0,
                cpu_ratio=0.2,
                io_bytes_per_s=10.0,
                child_events=0,
                output_novelty=1.0,
                voluntary_ctx_per_s=1.0,
                elapsed_s=t,
            )
        # Deadlock / stuck idle after burst
        return LivenessWindow(
            seconds_since_output=t - burst_duration_s + 60.0,
            cpu_ratio=0.0,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=None,
            voluntary_ctx_per_s=0.0,
            elapsed_s=t,
        )

    expected_kill = burst_duration_s + stall_window_s
    res = _run_scenario(sample, total_duration_s=300.0, stall_window_s=stall_window_s)
    assert res.killed is True
    assert res.kill_cause == "stalled"
    assert abs(res.kill_time_s - expected_kill) <= _DEFAULT_DT


# ---------------------------------------------------------------------------
# Scenario 9 — Alternating 20 s busy / 20 s idle -> completes
# ---------------------------------------------------------------------------


def test_scenario_09_alternating_busy_idle_completes() -> None:
    """Scenario 9: alternating 20 s busy / 20 s idle -> completes (pressure drains while busy)."""
    total_duration_s = 600.0  # 10 minutes
    cycle_s = 40.0
    busy_s = 20.0

    def sample(t: float) -> LivenessWindow:
        in_cycle = t % cycle_s
        if in_cycle < busy_s:
            # 20s busy: output recent, score 1.0
            return LivenessWindow(
                seconds_since_output=0.0,
                cpu_ratio=0.5,
                io_bytes_per_s=10.0,
                child_events=0,
                output_novelty=1.0,
                voluntary_ctx_per_s=2.0,
                elapsed_s=t,
            )
        # 20s idle: output stale, score 0.0
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


# ---------------------------------------------------------------------------
# Scenario 10 — Every signal except output unknown -> identical to baseline
# ---------------------------------------------------------------------------


def test_scenario_10_every_signal_except_output_unknown_matches_baseline() -> None:
    """Scenario 10: every signal except output unknown -> identical to output-only baseline."""
    dt = 2.0
    total_duration_s = 200.0
    si_base = StallIntegrator(theta=CFG.stall_theta)
    si_unkn = StallIntegrator(theta=CFG.stall_theta)

    elapsed = 0.0
    while elapsed < total_duration_s:
        elapsed += dt
        # Output age: oscillates between fresh and stale
        o = elapsed % 120.0

        # Baseline: output known, all other signals known zero
        win_base = LivenessWindow(
            seconds_since_output=o,
            cpu_ratio=0.0,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=CFG.repetitive_min,
            voluntary_ctx_per_s=0.0,
            elapsed_s=elapsed,
        )
        # Unknown: output known, all other signals None
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


# ---------------------------------------------------------------------------
# Scenario 11 — 45-min e2e with quiet waits (CPU ~0.01, w ~20/s) -> completes
# ---------------------------------------------------------------------------


def test_scenario_11_45min_e2e_quiet_waits_completes() -> None:
    """Scenario 11: 45-min e2e with quiet waits (CPU ~0.01, w ~20/s) -> completes."""
    total_duration_s = 2700.0  # 45 minutes
    pulse_interval_s = 180.0  # sparse output burst every 3 minutes

    def sample(t: float) -> LivenessWindow:
        in_pulse = (t % pulse_interval_s) < _DEFAULT_DT
        if in_pulse:
            # Test step completes and prints progress
            return LivenessWindow(
                seconds_since_output=0.0,
                cpu_ratio=0.1,
                io_bytes_per_s=5.0,
                child_events=1,
                output_novelty=0.9,
                voluntary_ctx_per_s=20.0,
                elapsed_s=t,
            )
        # Quiet wait on socket/browser: w ~20/s, CPU ~0.01, no output
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


# ---------------------------------------------------------------------------
# Scenario 12 — Poll-forever harness (w > 0, nothing else) -> no_progress
# ---------------------------------------------------------------------------


def test_scenario_12_poll_forever_harness_no_progress_at_15min() -> None:
    """Scenario 12: poll-forever harness (w > 0, nothing else) -> no_progress at 15 min."""

    def sample(t: float) -> LivenessWindow:
        # Polling socket: w = 20.0, no output, no CPU, no children
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


# ---------------------------------------------------------------------------
# Scenario 13 — Mid-run deadlock all children asleep -> stalled
# ---------------------------------------------------------------------------


def test_scenario_13_mid_run_deadlock_all_children_asleep_stalled() -> None:
    """Scenario 13: mid-run deadlock all children asleep -> stalled at stall_window after deadlock."""
    deadlock_at_s = 120.0
    stall_window_s = 90.0

    def sample(t: float) -> LivenessWindow:
        if t <= deadlock_at_s:
            # Normal e2e execution
            return LivenessWindow(
                seconds_since_output=0.0,
                cpu_ratio=0.1,
                io_bytes_per_s=10.0,
                child_events=1,
                output_novelty=1.0,
                voluntary_ctx_per_s=20.0,
                elapsed_s=t,
            )
        # Deadlock: all children asleep, w ~= 0, CPU = 0, no output
        return LivenessWindow(
            seconds_since_output=t - deadlock_at_s + 60.0,
            cpu_ratio=0.0,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=None,
            voluntary_ctx_per_s=0.0,
            elapsed_s=t,
        )

    expected_kill = deadlock_at_s + stall_window_s
    res = _run_scenario(sample, total_duration_s=400.0, stall_window_s=stall_window_s)
    assert res.killed is True
    assert res.kill_cause == "stalled"
    assert abs(res.kill_time_s - expected_kill) <= _DEFAULT_DT
