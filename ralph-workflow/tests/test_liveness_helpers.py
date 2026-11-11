from __future__ import annotations

import pytest

from ralph import timeout_defaults as _td
from ralph.process.manager._liveness_assessor import (
    LivenessConfig,
    LivenessWindow,
    StallIntegrator,
    SuspicionClock,
    default_liveness_config,
    step_with_assessment,
)

pytestmark = pytest.mark.timeout_seconds(30)

CFG: LivenessConfig = default_liveness_config()


def test_suspicion_clock_default_threshold_matches_constant() -> None:
    sc = SuspicionClock()
    assert sc.threshold_s == _td.EXEC_SUSPICIOUS_REPORT_S


def test_suspicion_clock_r4_sustained_60s_raises_fire() -> None:
    sc = SuspicionClock()
    last_fire_step = 0
    for i in range(60):
        last_fire_step = i + 1
        sustained = sc.step(strongest_rule="R4", dt=1.0)
        if sustained:
            break
    assert sustained is True
    assert sc.is_sustained is True
    assert sc.streak_s == pytest.approx(60.0)
    assert last_fire_step == 60


def test_suspicion_clock_r7_sustained_60s_raises_fire() -> None:
    sc = SuspicionClock()
    sustained = False
    for _ in range(60):
        sustained = sc.step(strongest_rule="R7", dt=1.0)
        if sustained:
            break
    assert sustained is True
    assert sc.streak_s == pytest.approx(60.0)


def test_suspicion_clock_break_resets_streak() -> None:
    sc = SuspicionClock()
    for _ in range(30):
        sc.step(strongest_rule="R4", dt=1.0)
    assert sc.streak_s == pytest.approx(30.0)
    sc.step(strongest_rule="R1", dt=1.0)
    assert sc.streak_s == pytest.approx(0.0)


def test_suspicion_clock_rejects_non_positive_threshold() -> None:
    with pytest.raises(ValueError):
        SuspicionClock(threshold_s=0.0)


def test_step_with_assessment_fills_post_integration_pressure() -> None:
    si = StallIntegrator(theta=CFG.stall_theta)
    win = LivenessWindow(
        seconds_since_output=CFG.output_recent_max_s + 5.0,
        cpu_ratio=CFG.cpu_busy_min - 0.01,
        io_bytes_per_s=CFG.io_kbps_inactive - 1.0,
        child_events=0,
        output_novelty=CFG.repetitive_min + 0.1,
        voluntary_ctx_per_s=CFG.awake_per_s_inactive - 0.1,
        elapsed_s=10.0,
    )
    v = step_with_assessment(win, CFG, si, dt=2.0, stall_window_s=90.0)
    assert v.pressure == pytest.approx(2.0)
    assert v.stall_window_s == pytest.approx(90.0)
    v2 = step_with_assessment(win, CFG, si, dt=2.0, stall_window_s=90.0)
    assert v2.pressure == pytest.approx(4.0)


def test_step_with_assessment_healthier_trace_drains_pressure() -> None:
    si = StallIntegrator(theta=CFG.stall_theta)
    si.step(verdict_score=0.0, dt=10.0)
    healthy = LivenessWindow(
        seconds_since_output=0.0,
        cpu_ratio=0.0,
        io_bytes_per_s=128.0,
        child_events=0,
        output_novelty=1.0,
        voluntary_ctx_per_s=0.0,
        elapsed_s=10.0,
    )
    v = step_with_assessment(healthy, CFG, si, dt=2.0, stall_window_s=90.0)
    assert v.pressure < 10.0
