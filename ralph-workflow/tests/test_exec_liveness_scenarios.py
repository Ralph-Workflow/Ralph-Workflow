"""Scenario trace library for the §3b pure deterministic liveness assessor.

14 pure signal/pressure traces evaluated directly against ``assess`` and
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
    """Scenario 7: repetitive same-line loop -> stalled via R6.

    The trace prints the same retry line every 1s (novelty ≤ 0.1) and
    has no I/O. R6 (``Repetitive AND NOT IoActive``) fires with canonical
    z=0.2 (PRODUCT_CRITERIA Part A / D2).
    Pressure rate = (0.5 - 0.2) / 0.5 = 0.6 per second.
    Expected kill at stall_window_s / 0.6 = 90 s / 0.6 = 150 s ± dt.
    """
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

    # Score = 0.2 (R6 fires with z=0.2; R1 suppressed by Repetitive=1).
    # Pressure rate = (0.5 - 0.2) / 0.5 = 0.6 per second.
    # Expected kill at 90.0 / 0.6 = 150.0 s ± dt.
    expected_kill_time = stall_window_s / 0.6
    res = _run_scenario(sample, total_duration_s=300.0, stall_window_s=stall_window_s)
    assert res.killed is True
    assert res.kill_cause == "stalled"
    assert abs(res.kill_time_s - expected_kill_time) <= _DEFAULT_DT


# ---------------------------------------------------------------------------
# Scenario 8 — Short burst of activity then idle -> stalled at stall_window
# ---------------------------------------------------------------------------


def test_scenario_08_short_burst_then_idle_stalled_at_window_after_activity() -> None:
    """Scenario 8: short burst then idle -> stalled at stall_window after activity stops.

    The closure derives ``seconds_since_output`` from a tracked
    ``last_output_at`` timestamp, matching production semantics in
    the ``ralph/process/manager/_liveness_sampler.py`` module (the
    production sampler block that computes
    ``seconds_since_output = max(0.0, now - last_output_at)`` from its
    tracked ``last_output_at``; no ``+60.0`` floor is applied). The
    previous test injected ``+60.0`` aging that artificially bypassed
    the ``OutputRecent`` ramp; with production semantics the ramp is
    honored and the empirical kill time is LATER than
    ``burst_duration_s + stall_window_s``.

    Named ramp constants used throughout the derivation below (paired
    with the architecture reconciliation at
    ``ralph/mcp/ARCHITECTURE.md`` §3 "Ramp-offset timing"; the same
    constants govern scenario 13 below):

      * ``ramp_start_s`` = age at which ``OutputRecent`` saturates at
        1.0 for fresh output, i.e. ``EXEC_OUTPUT_RECENT_MAX_S`` = 10 s.
      * ``ramp_end_s`` = age at which ``OutputRecent`` saturates at
        0.0, i.e. ``EXEC_OUTPUT_RECENT_MIN_S`` = 60 s.
      * ``ramp_half_threshold_age`` = 35 s — age at which the score
        first falls below ``EXEC_STALL_THETA`` = 0.5 under the linear
        membership; the per-step delta becomes non-positive until
        this age.
      * ``ramp_accrued_pressure`` = 12.5 s — stall pressure
        accumulated during the ``ages ∈ (35, 60]`` ramp window
        (trapezoidal integral of ``(age - 35)/25``).
      * ``ramp_offset`` = ``ramp_half_threshold_age - ramp_start_s``
        = 25 s — the dead-phase delay between "last output" and
        "stall pressure first accrues".

    Closed-form continuous derivation (relative to ``burst_stop``, the
    last output instant): the ``OutputRecent`` membership ramps 1 -> 0
    over output ages ``[EXEC_OUTPUT_RECENT_MAX_S, EXEC_OUTPUT_RECENT_MIN_S]
    = [10.0, 60.0]`` seconds. In the dead-phase signature
    (cpu = io = tree = awake = 0), only R1 (``OutputRecent AND NOT
    Repetitive``, z = 1.0) and R5 (NOT OutputRecent AND NOT CpuBusy AND
    NOT IoActive AND NOT TreeActive AND NOT Awake, z = 0.0) fire, with
    weights ``w1 = output_recent`` and ``w5 = 1 - output_recent``. The
    Sugeno score ``s = Σ wᵢ zᵢ / Σ wᵢ`` therefore collapses to
    ``s = output_recent``. Stall-pressure rate is
    ``max(0, (θ - s) / θ)`` per second with ``θ = 0.5``:

      * ages  0 -> 10 s after last output:  s = 1.0, rate = 0
      * ages 10 -> 35 s after last output:  s ramps 1.0 -> 0.5,
        rate = max(0, (0.5 - s) / 0.5) = 0  (negative branch clamps)
      * ages 35 -> 60 s after last output:  s ramps 0.5 -> 0.0,
        rate ramps 0 -> 1, accumulates
        ``ramp_accrued_pressure = ∫[35→60] (age - 35) / 25 d(age) = 12.5 s``
        of stall pressure (trapezoidal ramp integral)
      * ages ≥ 60 s after last output:      s = 0.0, rate = 1.0
        (full-rate phase)

    Total time from last output to kill is therefore
    ``ramp_end_s + (stall_window_s - ramp_accrued_pressure)`` =
    ``60 + (90 - 12.5) = 137.5 s`` continuous. For
    ``burst_duration_s = 30`` and the default 90 s stall window the
    closed form yields ``30 + 137.5 = 167.5 s`` continuous; the
    discrete ``_DEFAULT_DT`` = 2.0 s simulator in ``_run_scenario``
    integrates the same trapezoid on the coarse grid and lands at
    168 s (coarse-within-``_DEFAULT_DT`` of fine).

    Coarse-vs-fine empirical comparison: the test runs the same
    ``assess()`` + ``StallIntegrator`` forward on a fine ``dt``
    (0.01 s). The fine-dt emulator approximates the continuous ramp
    integration; fine kill ~= 167.49 s, coarse result (168 s) lies
    within one ``_DEFAULT_DT`` (2.0 s) of that. The assertion below
    compares the coarse and fine runs directly so the bound is
    self-consistent regardless of how the ramp integration shifts
    with future breakpoint tuning.

    Reconciliation against criterion C3 / the spec-table wording
    "``stalled`` at ``stall_window`` after activity stops": the shipped
    kill time is NOT within that tolerance, and PROOF 1 in
    ``ralph/process/manager/_liveness_assessor.py`` shows it CANNOT be
    by any aggregate or any theta. Reading the criterion's own anchor
    ("activity stops" = the last output byte at ``t = 30``), the closed
    form above gives ``30 + 137.5 = 167.5 s`` continuous (168 s on the
    coarse grid) against the criterion's ``30 + 90 + 2 = 122 s``
    tolerance — 45.5 s late. Even measured from the most charitable
    anchor (pressure onset at ``o = 35``) it is ``90 + 12.5 = 102.5 s``,
    still 10.5 s late. The floor is
    ``last_output + EXEC_OUTPUT_RECENT_MAX_S + stall_window``
    (``ramp_start_s`` is exactly ``EXEC_OUTPUT_RECENT_MAX_S``) =
    ``30 + 100 = 130 s``, asserted below, so 8 s of the 45.5 s
    deviation is already unreachable in principle and the remaining
    37.5 s comes from the mandated linear ramp: ``25`` s of extra flat
    top (``ramp_start_s`` = 10 up to ``ramp_half_threshold_age`` = 35)
    plus the ``12.5`` s ramp penalty. Criterion C3 IS met literally for
    the never-output shape (scenario 1 above).
    """
    burst_duration_s = 30.0
    stall_window_s = 90.0

    state: dict[str, float] = {"last_output_t": -1.0}

    def sample(t: float) -> LivenessWindow:
        if t <= burst_duration_s:
            # Active work: fresh output — capture this sample's timestamp
            # as the last output instant (matches production: when output
            # is observed, _liveness_sampler records now as last_output_at).
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
        # Deadlock / stuck idle after burst — derive age from last_output_t.
        return LivenessWindow(
            seconds_since_output=max(0.0, t - state["last_output_t"]),
            cpu_ratio=0.0,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=None,
            voluntary_ctx_per_s=0.0,
            elapsed_s=t,
        )

    # Empirical derivation — run the same _run_scenario harness on a
    # fine dt so the ramp integration error is well below _DEFAULT_DT.
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

    # PROOF 1 floor, locked executably: for ``age <=
    # EXEC_OUTPUT_RECENT_MAX_S`` the score saturates at ``z1 = 1.0 >
    # theta`` so pressure cannot rise, and afterwards it rises at most at
    # 1 s/s. No aggregate and no theta can kill earlier than
    # ``last_output + EXEC_OUTPUT_RECENT_MAX_S + stall_window``. Asserted
    # as a LOWER bound, so it can only ever tighten the deviation, never
    # hide it.
    proof1_floor_s = burst_duration_s + CFG.output_recent_min_s + stall_window_s
    assert fine_res.kill_time_s >= proof1_floor_s - 0.05, (
        f"PROOF 1 floor violated: kill at {fine_res.kill_time_s!r} is earlier "
        f"than last_output + EXEC_OUTPUT_RECENT_MAX_S + stall_window = {proof1_floor_s!r}"
    )
    # ... and that floor is already OUTSIDE the criterion's tolerance,
    # which is what makes the deviation provable rather than a tuning miss.
    criterion_deadline_s = burst_duration_s + stall_window_s + _DEFAULT_DT
    assert proof1_floor_s > criterion_deadline_s
    assert res.kill_time_s > criterion_deadline_s


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
    """Scenario 13: mid-run deadlock all children asleep -> stalled.

    The closure derives ``seconds_since_output`` from a tracked
    ``last_output_at`` timestamp, matching production semantics in
    the ``ralph/process/manager/_liveness_sampler.py`` module (the
    production sampler block that computes
    ``seconds_since_output = max(0.0, now - last_output_at)`` from its
    tracked ``last_output_at``; no ``+60.0`` floor is applied). The
    previous test injected ``+60.0`` aging that artificially bypassed
    the ``OutputRecent`` ramp; with production semantics the ramp is
    honored and the empirical kill time is LATER than
    ``deadlock_at_s + stall_window_s``.

    Scenario 13 is the time-shifted version of scenario 8 — both
    share the same all-idle dead-phase signature (cpu = 0, io = 0,
    child = 0, voluntary = 0) and therefore the same ramp dynamics
    under the default ``OutputRecent`` breakpoints
    (``EXEC_OUTPUT_RECENT_MAX_S = 10.0``, ``EXEC_OUTPUT_RECENT_MIN_S
    = 60.0``). Named ramp constants used throughout the derivation
    below:

      * ``ramp_end_s`` = age at which ``OutputRecent`` saturates at 0
        for the dead phase, i.e. ``EXEC_OUTPUT_RECENT_MIN_S`` = 60 s.
      * ``ramp_half_threshold_age`` = age at which the score first
        falls below ``EXEC_STALL_THETA`` = 0.5, computed as
        ``(ramp_end_s - 25) = 35`` s under the linear-ramp membership.
      * ``ramp_accrued_pressure`` = stall pressure accumulated
        during the ``ages ∈ (ramp_half_threshold_age, ramp_end_s]``
        window (the trapezoidal integral of the per-step delta), =
        12.5 s under the shipped constants.
      * ``ramp_offset`` = ``ramp_half_threshold_age - ``
        ``EXEC_OUTPUT_RECENT_MAX_S`` = 35 - 10 = 25 s; the dead-phase
        delay between "last output" and "stall pressure first accrues".

    Closed-form continuous derivation (relative to the
    last output instant):

      * ages  0 -> 35 s: rate = 0  (no pressure accrues — score ≥ 0.5)
      * ages 35 -> 60 s: rate ramps 0 -> 1, accumulates
        ``ramp_accrued_pressure = 12.5 s`` of stall pressure
      * ages ≥ 60 s: rate = 1.0  (full-rate phase, score = 0)

    Total time from last output to kill is
    ``ramp_end_s + (stall_window_s - ramp_accrued_pressure)`` =
    ``60 + (90 - 12.5) = 137.5 s`` continuous. For
    ``deadlock_at_s = 120`` and ``stall_window_s = 90`` the closed
    form yields ``120 + 137.5 = 257.5 s`` continuous; the discrete
    ``_DEFAULT_DT`` = 2.0 s simulator in ``_run_scenario`` integrates
    the same trapezoid on the coarse grid and lands at 258 s
    (coarse-within-``_DEFAULT_DT`` of fine).

    Coarse-vs-fine empirical comparison: the test runs ``assess()`` +
    ``StallIntegrator`` forward on a fine ``dt`` (0.01 s) to
    approximate the continuous ramp integration. Fine kill ~=
    257.49 s, coarse result (258 s) lies within one ``_DEFAULT_DT``
    (2.0 s) of that. The assertion compares the coarse and fine
    runs directly so the bound is self-consistent regardless of how
    the ramp integration shifts with future breakpoint tuning.

    Reconciliation against criterion C3 / the spec-table wording
    "``stalled`` at ``stall_window`` after the deadlock": the shipped
    kill time is NOT within that tolerance, and PROOF 1 in
    ``ralph/process/manager/_liveness_assessor.py`` shows it CANNOT be
    by any aggregate or any theta. Reading the criterion's own anchor
    ("after the deadlock" = the last output byte at ``t = 120``), the
    closed form gives ``120 + 137.5 = 257.5 s`` continuous (258 s on
    the coarse grid) against the criterion's ``120 + 90 + 2 = 212 s``
    tolerance — 45.5 s late, the same offset as scenario 8 because both
    traces share the all-idle dead-phase signature. The proven floor
    ``last_output + EXEC_OUTPUT_RECENT_MAX_S + stall_window`` =
    ``120 + 100 = 220 s`` is asserted below and already sits 8 s past
    the criterion deadline, so 8 s of the deviation is unreachable in
    principle and the remaining 37.5 s is the mandated linear ramp
    (``25`` s of extra flat top plus the ``12.5`` s ramp penalty).
    Criterion C3 IS met literally for the never-output shape
    (scenario 1).
    """
    deadlock_at_s = 120.0
    stall_window_s = 90.0

    state: dict[str, float] = {"last_output_t": -1.0}

    def sample(t: float) -> LivenessWindow:
        if t <= deadlock_at_s:
            # Normal e2e execution — capture this sample's timestamp as
            # the last output instant (matches production).
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
        # Deadlock: all children asleep, w ~= 0, CPU = 0, no output.
        return LivenessWindow(
            seconds_since_output=max(0.0, t - state["last_output_t"]),
            cpu_ratio=0.0,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=None,
            voluntary_ctx_per_s=0.0,
            elapsed_s=t,
        )

    # Empirical derivation — run the same _run_scenario harness on a
    # fine dt so the ramp integration error is well below _DEFAULT_DT.
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

    # PROOF 1 floor, locked executably (see scenario 8 for the argument):
    # ``t_kill >= last_output + EXEC_OUTPUT_RECENT_MAX_S + stall_window``
    # for any aggregate and any theta, and that floor is itself already
    # past the criterion's ``stall_window + 1 interval`` deadline.
    proof1_floor_s = deadlock_at_s + CFG.output_recent_min_s + stall_window_s
    assert fine_res.kill_time_s >= proof1_floor_s - 0.05, (
        f"PROOF 1 floor violated: kill at {fine_res.kill_time_s!r} is earlier "
        f"than last_output + EXEC_OUTPUT_RECENT_MAX_S + stall_window = {proof1_floor_s!r}"
    )
    criterion_deadline_s = deadlock_at_s + stall_window_s + _DEFAULT_DT
    assert proof1_floor_s > criterion_deadline_s
    assert res.kill_time_s > criterion_deadline_s


# ---------------------------------------------------------------------------
# Scenario 14 — Never emitted any output (``last_output_at is None``)
# ---------------------------------------------------------------------------


def test_scenario_14_never_output_shape_stalled_at_stall_window() -> None:
    """Scenario 14: the never-output shape -> ``stalled`` at stall_window ± dt.

    Scenarios 1 / 8 / 13 all model a trace whose ``last_output_at`` HAS been
    set and whose output then ages past the ``OutputRecent`` breakpoints.
    This scenario locks the one shape acceptance criterion C3 ("An idle
    command is killed with cause ``stalled`` within ``stall_window`` plus one
    sample interval") is literally reachable on, and the shape PROOF 1 in
    ``ralph/process/manager/_liveness_assessor.py`` names but did not have a
    scenario behind it.

    The trace is built exactly the way ``TreeSampler.sample`` builds a
    never-output window in production
    (``ralph/process/manager/_liveness_sampler.py:470-474``): a job whose
    tracked ``last_output_at`` is still ``None`` reports
    ``seconds_since_output = None`` — not ``0.0`` and not an aged value — while
    every other signal is a real reading from a real idle tree (cpu 0, io 0,
    no children, no voluntary context switches). ``output_novelty`` stays
    ``None`` because the evidence window holds no output records
    (``_compute_output_novelty`` needs >= 20 lines).

    Mechanism the criterion rests on: ``_output_recent(None) == 0.0``
    (``ralph/process/manager/_liveness_assessor.py:339-340``), so ``R5`` (idle,
    ``z5 = 0.0``) is the only rule with non-zero weight and the score is 0.0
    from the very first sample — the ``OutputRecent`` ramp of scenarios 8 / 13
    never engages. Stall pressure therefore accrues at the maximum unit rate,
    ``dt`` per step, and the crossing lands on the stall window itself.

    Two consequences are locked together with the timing, because together
    they are the whole criterion:

    * the kill time lands ON ``stall_window_s`` (not the
      ``stall_window_s + 47.5`` of the burst-then-idle shape), within one
      sample interval; and
    * the cause is ``stalled``, not ``no_progress`` — the idle shape is
      classified ``stalled`` rather than ``suspicious``, so the D4 suspicion
      clock never accrues and the 15-minute no-progress window is unreachable
      from this trace.
    """
    stall_window_s = 90.0

    def sample(t: float) -> LivenessWindow:
        # Production never-output window: ``last_output_at is None`` so the
        # sampler reports ``None``, not ``0.0``. Everything else is the real
        # reading a fully idle, silent, childless tree produces.
        return LivenessWindow(
            seconds_since_output=None,
            cpu_ratio=0.0,
            io_bytes_per_s=0.0,
            child_events=0,
            output_novelty=None,
            voluntary_ctx_per_s=0.0,
            elapsed_s=t,
        )

    # The very first sample already reads as the canonical idle shape, with no
    # ramp to traverse: R5 alone, score 0.0, not suspicious.
    first = assess(sample(_DEFAULT_DT), CFG)
    assert first.score == 0.0
    assert first.strongest_rule == "R5"
    assert first.verdict_kind == "stalled"
    assert first.suspicious is False

    res = _run_scenario(sample, total_duration_s=200.0, stall_window_s=stall_window_s)
    assert res.killed is True
    assert res.kill_cause == "stalled"
    assert abs(res.kill_time_s - stall_window_s) <= _DEFAULT_DT
    # ... and it lands exactly on the window on the shipped ``_DEFAULT_DT``
    # grid: pressure accrues at one second per second, so the crossing is the
    # stall window itself.
    assert res.kill_time_s == stall_window_s
    # The idle shape never accrues suspicion, so no-progress is unreachable
    # from this trace and ``stalled`` is the only cause it can die of.
    assert res.suspicious_reported is False
