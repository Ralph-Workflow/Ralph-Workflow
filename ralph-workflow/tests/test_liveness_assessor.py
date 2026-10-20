"""Deterministic tests for the §3b liveness assessor.

Table tests over every membership breakpoint, the AND/OR/NOT algebra,
the per-rule firing pattern (R1..R7), and the StallIntegrator dynamics.
All assertions are arithmetic — no subprocess, no clock, no I/O.
"""

from __future__ import annotations

import pytest

from ralph.process.manager._liveness_assessor import (
    LivenessConfig,
    LivenessWindow,
    StallIntegrator,
    _awake,
    _cpu_busy,
    _evaluate_rules,
    _io_active,
    _output_recent,
    _repetitive,
    _tree_active,
    assess,
    default_liveness_config,
)

# Defaults shared by every table test. We never mutate this; each test that
# wants a custom config constructs its own dataclass.
CFG: LivenessConfig = default_liveness_config()


# ---------------------------------------------------------------------------
# Membership breakpoints + midpoints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("o", "expected"),
    [
        (0.0, 1.0),
        (CFG.output_recent_min_s, 1.0),
        ((CFG.output_recent_min_s + CFG.output_recent_max_s) / 2.0, 0.5),
        (CFG.output_recent_max_s, 0.0),
        (CFG.output_recent_max_s + 5.0, 0.0),
    ],
)
def test_output_recent_breakpoints(o: float, expected: float) -> None:
    assert _output_recent(o, CFG) == pytest.approx(expected)


def test_output_recent_unknown_signal() -> None:
    assert _output_recent(None, CFG) == 0.0


@pytest.mark.parametrize(
    ("c_val", "expected"),
    [
        (0.0, 0.0),
        (CFG.cpu_busy_min, 0.0),
        ((CFG.cpu_busy_min + CFG.cpu_busy_max) / 2.0, 0.5),
        (CFG.cpu_busy_max, 1.0),
        (CFG.cpu_busy_max + 0.1, 1.0),
    ],
)
def test_cpu_busy_breakpoints(c_val: float, expected: float) -> None:
    assert _cpu_busy(c_val, CFG) == pytest.approx(expected)


def test_cpu_busy_unknown_signal() -> None:
    assert _cpu_busy(None, CFG) == 0.0


@pytest.mark.parametrize(
    ("io", "expected"),
    [
        (0.0, 0.0),
        (CFG.io_kbps_inactive, 0.0),
        ((CFG.io_kbps_inactive + CFG.io_kbps_active) / 2.0, 0.5),
        (CFG.io_kbps_active, 1.0),
        (CFG.io_kbps_active + 100.0, 1.0),
    ],
)
def test_io_active_breakpoints(io: float, expected: float) -> None:
    assert _io_active(io, CFG) == pytest.approx(expected)


def test_io_active_unknown_signal() -> None:
    assert _io_active(None, CFG) == 0.0


@pytest.mark.parametrize(
    ("t", "expected"),
    [
        (0, 0.0),
        (1, 1.0),
        (5, 1.0),
    ],
)
def test_tree_active_breakpoints(t: int, expected: float) -> None:
    assert _tree_active(t, CFG) == pytest.approx(expected)


def test_tree_active_unknown_signal() -> None:
    assert _tree_active(None, CFG) == 0.0


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (0.0, 1.0),
        (CFG.repetitive_max, 1.0),
        ((CFG.repetitive_max + CFG.repetitive_min) / 2.0, 0.5),
        (CFG.repetitive_min, 0.0),
        (CFG.repetitive_min + 0.1, 0.0),
    ],
)
def test_repetitive_breakpoints(n: float, expected: float) -> None:
    assert _repetitive(n, CFG) == pytest.approx(expected)


def test_repetitive_unknown_signal() -> None:
    assert _repetitive(None, CFG) == 0.0


@pytest.mark.parametrize(
    ("w", "expected"),
    [
        (0.0, 0.0),
        (CFG.awake_per_s_inactive, 0.0),
        ((CFG.awake_per_s_inactive + CFG.awake_per_s_active) / 2.0, 0.5),
        (CFG.awake_per_s_active, 1.0),
        (CFG.awake_per_s_active + 5.0, 1.0),
    ],
)
def test_awake_breakpoints(w: float, expected: float) -> None:
    assert _awake(w, CFG) == pytest.approx(expected)


def test_awake_unknown_signal() -> None:
    assert _awake(None, CFG) == 0.0


# ---------------------------------------------------------------------------
# AND/OR/NOT algebra on rule firings
# ---------------------------------------------------------------------------


def test_evaluate_rules_all_zero_memberships_fires_only_r5() -> None:
    """When every membership reads 0, only R5 (idle) fires; strongest = R5.

    Distinct from the ``all-None`` semantics (D5 fail-closed via ``assess``):
    the membership layer folds unknown → 0, so ``_evaluate_rules`` sees a
    known-zero trace, the NOTs all become 1, and the rule evaluator returns
    R5 with weight 1. The ``assess`` layer applies the per-rule gating that
    uses raw signal None-vs-known state, so an ``assess`` call with all-None
    inputs produces "unknown" verdict (see ``test_assess_every_signal_none_*``).
    """
    weights, strongest = _evaluate_rules(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    # All NOTs are (1 - 0) = 1, so R5 = min(1, 1, 1, 1, 1) = 1.
    assert weights[4] == pytest.approx(1.0)
    assert strongest == "R5"
    # All other rules have at least one required-positive input at 0 → weight 0.
    for i, w in enumerate(weights):
        if i == 4:
            continue
        assert w == 0.0


def test_evaluate_rules_r1_when_recent_and_novel() -> None:
    """R1 fires alone when OutputRecent=1, Repetitive=0."""
    weights, strongest = _evaluate_rules(1.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert weights[0] == pytest.approx(1.0)
    assert weights[4] == pytest.approx(0.0)
    assert strongest == "R1"


def test_evaluate_rules_r5_min_over_zero_signals() -> None:
    """R5 (idle) fires when every known signal is zero (== KNOWN idle).

    This contrasts with all-None above: KNOWN absence is evidence of
    idle (R5 fires at z=0); UNKNOWN absence cannot make the rule fire
    (D5 — fail closed).
    """
    weights, _ = _evaluate_rules(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert weights[4] == pytest.approx(1.0)


def test_evaluate_rules_not_inverts_membership() -> None:
    """NOT x = 1 - x is built into R5: OutputRecent=1 forces R5=0."""
    weights, _ = _evaluate_rules(1.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert weights[4] == 0.0


def test_evaluate_rules_min_with_mixed_signal() -> None:
    """R4: CpuBusy AND NOT OutputRecent AND NOT IoActive = min of three."""
    weights, _ = _evaluate_rules(0.5, 1.0, 0.0, 0.0, 0.0, 0.0)
    assert weights[3] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Verdict construction — score formula + verdict_kind selection
# ---------------------------------------------------------------------------


def test_assess_every_signal_none_is_unknown() -> None:
    """Σw < 0.05 → verdict = unknown; score = 0."""
    win = LivenessWindow(
        seconds_since_output=None,
        cpu_ratio=None,
        io_bytes_per_s=None,
        child_events=None,
        output_novelty=None,
        voluntary_ctx_per_s=None,
        elapsed_s=1.0,
    )
    v = assess(win, CFG)
    assert v.score == 0.0
    assert v.verdict_kind == "unknown"
    assert v.strongest_rule == ""
    assert v.suspicious is False


def test_assess_r4_silent_compute_alone() -> None:
    """Rule 4 (silent compute) fires on cpu=0.8, others None → score ~0.6."""
    win = LivenessWindow(
        seconds_since_output=None,
        cpu_ratio=0.8,
        io_bytes_per_s=None,
        child_events=0,
        output_novelty=None,
        voluntary_ctx_per_s=None,
        elapsed_s=10.0,
    )
    v = assess(win, CFG)
    assert v.score == pytest.approx(0.6)
    assert v.verdict_kind == "suspicious"
    assert v.strongest_rule == "R4"
    assert v.suspicious is True


def test_assess_r5_idle_stuck_alone() -> None:
    """R5 (idle stuck) fires when every signal is KNOWN AND ZERO → score 0.

    R5 has the strictest gating: idle is a strong negative claim, so every
    input must be KNOWN to be inactive before R5 fires. Setting any input to
    ``None`` (unknown) prevents the rule from firing — see D5 ("unknown
    means no evidence of work"). The test sets every signal to a known-zero
    value so R5 fires alone.
    """
    win = LivenessWindow(
        seconds_since_output=CFG.output_recent_max_s + 5.0,  # KNOWN stale => OutputRecent=0
        cpu_ratio=CFG.cpu_busy_min - 0.01,                   # KNOWN idle   => CpuBusy=0
        io_bytes_per_s=CFG.io_kbps_inactive - 1.0,           # KNOWN idle   => IoActive=0
        child_events=0,                                     # KNOWN idle   => TreeActive=0
        output_novelty=CFG.repetitive_min + 0.1,             # KNOWN novel  => Repetitive=0
        voluntary_ctx_per_s=CFG.awake_per_s_inactive - 0.1,  # KNOWN asleep => Awake=0
        elapsed_s=10.0,
    )
    v = assess(win, CFG)
    assert v.score == 0.0
    assert v.verdict_kind == "stalled"
    assert v.strongest_rule == "R5"


def test_assess_r6_repetitive_alone() -> None:
    """Rule 6 (repetitive) fires when Repetitive=1, others not active → score 0.2.

    To isolate R6 from R5, set seconds_since_output=0 (OutputRecent=1, which
    forces NOT OutputRecent=0 and so disables R5). The tree signal is also
    known-zero so R3 doesn't fire.
    """
    win = LivenessWindow(
        seconds_since_output=0.0,
        cpu_ratio=0.0,
        io_bytes_per_s=0.0,
        child_events=0,
        output_novelty=0.05,
        voluntary_ctx_per_s=0.0,
        elapsed_s=10.0,
    )
    v = assess(win, CFG)
    assert v.score == pytest.approx(0.2)
    assert v.strongest_rule == "R6"
    assert v.verdict_kind == "working"


def test_assess_r7_waiting_alone() -> None:
    """Rule 7 (waiting) fires on awake=10, others None → score 0.55."""
    win = LivenessWindow(
        seconds_since_output=None,
        cpu_ratio=None,
        io_bytes_per_s=None,
        child_events=0,
        output_novelty=None,
        voluntary_ctx_per_s=10.0,
        elapsed_s=10.0,
    )
    v = assess(win, CFG)
    assert v.score == pytest.approx(0.55)
    assert v.verdict_kind == "suspicious"
    assert v.strongest_rule == "R7"
    assert v.suspicious is True


def test_assess_r1_r2_combo_is_working() -> None:
    """R1 + R2 both firing (recent + I/O) blends to ~0.95."""
    win = LivenessWindow(
        seconds_since_output=0.0,
        cpu_ratio=0.0,            # KNOWN-not-busy so R4 doesn't fire on cpu alone
        io_bytes_per_s=CFG.io_kbps_active,
        child_events=0,           # KNOWN no children so R3 doesn't fire
        output_novelty=1.0,       # KNOWN novel so R1's NOT Repetitive is 1
        voluntary_ctx_per_s=0.0,  # KNOWN asleep so R7 doesn't fire
        elapsed_s=10.0,
    )
    v = assess(win, CFG)
    assert v.score == pytest.approx((1.0 + 0.9) / 2.0)
    assert v.verdict_kind == "working"


def test_assess_signals_dict_round_trips_window() -> None:
    """Verdict.signals carries every raw window field including unknowns."""
    win = LivenessWindow(
        seconds_since_output=3.5,
        cpu_ratio=0.1,
        io_bytes_per_s=None,
        child_events=4,
        output_novelty=0.9,
        voluntary_ctx_per_s=None,
        elapsed_s=12.5,
    )
    v = assess(win, CFG)
    assert v.signals["seconds_since_output"] == 3.5
    assert v.signals["cpu_ratio"] == 0.1
    assert v.signals["io_bytes_per_s"] is None
    assert v.signals["child_events"] == 4.0
    assert v.signals["output_novelty"] == 0.9
    assert v.signals["voluntary_ctx_per_s"] is None
    assert v.elapsed_s == 12.5


def test_assess_score_is_non_negative() -> None:
    """Score >= 0 — every z_i >= 0 and the score is a weighted average."""
    win = LivenessWindow(
        seconds_since_output=0.0,
        cpu_ratio=1.0,
        io_bytes_per_s=CFG.io_kbps_active,
        child_events=10,
        output_novelty=1.0,
        voluntary_ctx_per_s=CFG.awake_per_s_active,
        elapsed_s=10.0,
    )
    v = assess(win, CFG)
    assert v.score >= 0.0


# ---------------------------------------------------------------------------
# StallIntegrator
# ---------------------------------------------------------------------------


def test_stall_integrator_initial_pressure_is_zero() -> None:
    si = StallIntegrator(theta=CFG.stall_theta)
    assert si.pressure == 0.0
    assert si.should_kill(0.0) is True  # 0 >= 0


def test_stall_integrator_step_returns_new_p() -> None:
    si = StallIntegrator(theta=CFG.stall_theta)
    p1 = si.step(verdict_score=0.0, dt=1.0)
    assert p1 == pytest.approx(1.0)
    assert si.pressure == pytest.approx(1.0)


def test_stall_integrator_monotonic_when_score_below_theta() -> None:
    """P never decreases when verdict_score < theta (idle trace)."""
    si = StallIntegrator(theta=CFG.stall_theta)
    p = 0.0
    for _ in range(50):
        new_p = si.step(verdict_score=0.0, dt=1.0)
        assert new_p >= p
        p = new_p


def test_stall_integrator_drains_when_score_above_theta() -> None:
    """P decreases when verdict_score > theta."""
    si = StallIntegrator(theta=0.5)
    si.step(verdict_score=0.0, dt=10.0)
    assert si.pressure > 0.0
    for _ in range(5):
        si.step(verdict_score=1.0, dt=1.0)
    assert si.pressure < 10.0


def test_stall_integrator_pressure_never_negative() -> None:
    """P is clamped at 0; long high-score trace never yields negative pressure."""
    si = StallIntegrator(theta=0.5)
    for _ in range(1_000):
        si.step(verdict_score=1.0, dt=1.0)
    assert si.pressure >= 0.0


def test_stall_integrator_should_kill_at_threshold() -> None:
    si = StallIntegrator(theta=0.5)
    stall_window_s = 7.0
    for _ in range(int(stall_window_s)):
        si.step(verdict_score=0.0, dt=1.0)
    assert si.pressure == pytest.approx(stall_window_s)
    assert si.should_kill(stall_window_s) is True
    assert si.should_kill(stall_window_s + 0.0001) is False
    assert si.should_kill(stall_window_s - 0.0001) is True


def test_stall_integrator_should_kill_below_threshold() -> None:
    si = StallIntegrator(theta=0.5)
    for _ in range(6):
        si.step(verdict_score=0.0, dt=1.0)
    assert si.pressure == pytest.approx(6.0)
    assert si.should_kill(7.0) is False


def test_stall_integrator_all_idle_trace_kills_at_window() -> None:
    """Best test of all: an all-idle trace reaches kill at stall_window_s ± 1 interval.

    With ``dt=1.0`` and ``stall_window_s=60``, each idle step adds exactly
    1.0 to P. The kill turns True at step ``stall_window_s`` and stays True
    thereafter; one step before it is False.
    """
    stall_window_s = 60.0
    dt = 1.0
    n_to_kill = int(stall_window_s)  # 60 steps to reach threshold

    si = StallIntegrator(theta=CFG.stall_theta)

    # Drive just under the kill window — must NOT kill.
    for _ in range(n_to_kill - 1):
        si.step(verdict_score=0.0, dt=dt)
    assert si.pressure == pytest.approx(n_to_kill - 1)
    assert si.should_kill(stall_window_s) is False

    # One more step pushes pressure to stall_window_s — kill becomes True.
    si.step(verdict_score=0.0, dt=dt)
    assert si.pressure == pytest.approx(n_to_kill)
    assert si.should_kill(stall_window_s) is True