"""Sanity tests for ``SuspicionClock`` and ``step_with_assessment``.

The hypothesis property tests that drive the liveness assessor's score
formula, the stall-pressure integration, and the kill-cause decisions
live in ``test_liveness_properties.py``. The tests here cover the smaller
helper surfaces introduced alongside the §3b fix:

* ``SuspicionClock`` — sustained-suspicion tracker that fires
  ``is_sustained`` once ``threshold_s`` of continuous R4/R7 has
  accumulated. Defaults to ``EXEC_SUSPICIOUS_REPORT_S`` (60 s).
* ``step_with_assessment`` — single-cycle helper that runs
  ``assess`` + ``StallIntegrator.step`` and returns a verdict with
  the post-integration pressure and stall window filled in.

Both helpers are deterministic (no I/O, no clock, no subprocess); the
tests stay small and avoid ``time.sleep``.
"""

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


# ---------------------------------------------------------------------------
# SuspicionClock
# ---------------------------------------------------------------------------


def test_suspicion_clock_default_threshold_matches_constant() -> None:
    """SuspicionClock defaults to ``EXEC_SUSPICIOUS_REPORT_S`` (60 s)."""
    sc = SuspicionClock()
    assert sc.threshold_s == _td.EXEC_SUSPICIOUS_REPORT_S


def test_suspicion_clock_r4_sustained_60s_raises_fire() -> None:
    """60 s of continuous R4 → ``streak_s >= threshold_s`` returns True."""
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
    assert last_fire_step == 60  # exactly at threshold


def test_suspicion_clock_r7_sustained_60s_raises_fire() -> None:
    """60 s of continuous R7 also fires (not just R4)."""
    sc = SuspicionClock()
    sustained = False
    for _ in range(60):
        sustained = sc.step(strongest_rule="R7", dt=1.0)
        if sustained:
            break
    assert sustained is True
    assert sc.streak_s == pytest.approx(60.0)


def test_suspicion_clock_break_resets_streak() -> None:
    """A non-R4/R7 tick resets the streak to 0."""
    sc = SuspicionClock()
    for _ in range(30):
        sc.step(strongest_rule="R4", dt=1.0)
    assert sc.streak_s == pytest.approx(30.0)
    sc.step(strongest_rule="R1", dt=1.0)
    assert sc.streak_s == pytest.approx(0.0)


def test_suspicion_clock_rejects_non_positive_threshold() -> None:
    """Defensive: a non-positive threshold is rejected at construction."""
    with pytest.raises(ValueError):
        SuspicionClock(threshold_s=0.0)


# ---------------------------------------------------------------------------
# step_with_assessment
# ---------------------------------------------------------------------------


def test_step_with_assessment_fills_post_integration_pressure() -> None:
    """``step_with_assessment`` returns a verdict with integrated pressure."""
    si = StallIntegrator(theta=CFG.stall_theta)
    # Idle trace -> score=0 -> pressure rate = dt * (theta - 0) / theta.
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
    # 2.0 * (0.5 - 0) / 0.5 = 2.0
    assert v.pressure == pytest.approx(2.0)
    assert v.stall_window_s == pytest.approx(90.0)
    # Step again
    v2 = step_with_assessment(win, CFG, si, dt=2.0, stall_window_s=90.0)
    assert v2.pressure == pytest.approx(4.0)


def test_step_with_assessment_healthier_trace_drains_pressure() -> None:
    """Healthier samples (score ≥ θ) must DRAIN the integrator; ``step_with_assessment`` reports the new pressure."""
    si = StallIntegrator(theta=CFG.stall_theta)
    si.step(verdict_score=0.0, dt=10.0)  # baseline P = 10.0
    # Healthy: score 1.0 → rate = dt · (0.5 - 1.0) / 0.5 = -dt → drains.
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
