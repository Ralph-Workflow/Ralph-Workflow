from __future__ import annotations

import pytest

from ralph.process.manager import (
    LivenessConfig,
    LivenessWindow,
    StallIntegrator,
    assess,
    awake,
    cpu_busy,
    default_liveness_config,
    evaluate_rules,
    io_active,
    output_recent,
    repetitive,
    tree_active,
)

CFG: LivenessConfig = default_liveness_config()


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
    assert output_recent(o, CFG) == pytest.approx(expected)


def test_output_recent_unknown_signal() -> None:
    assert output_recent(None, CFG) == 0.0


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
    assert cpu_busy(c_val, CFG) == pytest.approx(expected)


def test_cpu_busy_unknown_signal() -> None:
    assert cpu_busy(None, CFG) == 0.0


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
    assert io_active(io, CFG) == pytest.approx(expected)


def test_io_active_unknown_signal() -> None:
    assert io_active(None, CFG) == 0.0


@pytest.mark.parametrize(
    ("t", "expected"),
    [
        (0, 0.0),
        (1, 1.0),
        (5, 1.0),
    ],
)
def test_tree_active_breakpoints(t: int, expected: float) -> None:
    assert tree_active(t, CFG) == pytest.approx(expected)


def test_tree_active_unknown_signal() -> None:
    assert tree_active(None, CFG) == 0.0


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
    assert repetitive(n, CFG) == pytest.approx(expected)


def test_repetitive_unknown_signal() -> None:
    assert repetitive(None, CFG) == 0.0


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
    assert awake(w, CFG) == pytest.approx(expected)


def test_awake_unknown_signal() -> None:
    assert awake(None, CFG) == 0.0


def test_evaluate_rules_all_zero_memberships_fires_only_r5() -> None:
    weights, strongest = evaluate_rules(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert weights[4] == pytest.approx(1.0)
    assert strongest == "R5"
    for i, w in enumerate(weights):
        if i == 4:
            continue
        assert w == 0.0


def test_evaluate_rules_r1_when_recent_and_novel() -> None:
    weights, strongest = evaluate_rules(1.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert weights[0] == pytest.approx(1.0)
    assert weights[4] == pytest.approx(0.0)
    assert strongest == "R1"


def test_evaluate_rules_r5_min_over_zero_signals() -> None:
    weights, _ = evaluate_rules(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert weights[4] == pytest.approx(1.0)


def test_evaluate_rules_not_inverts_membership() -> None:
    weights, _ = evaluate_rules(1.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert weights[4] == 0.0


def test_evaluate_rules_min_with_mixed_signal() -> None:
    weights, _ = evaluate_rules(0.5, 1.0, 0.0, 0.0, 0.0, 0.0)
    assert weights[3] == pytest.approx(0.5)


def test_assess_every_signal_none_is_unknown() -> None:
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
    win = LivenessWindow(
        seconds_since_output=CFG.output_recent_max_s + 5.0,
        cpu_ratio=CFG.cpu_busy_min - 0.01,
        io_bytes_per_s=CFG.io_kbps_inactive - 1.0,
        child_events=0,
        output_novelty=CFG.repetitive_min + 0.1,
        voluntary_ctx_per_s=CFG.awake_per_s_inactive - 0.1,
        elapsed_s=10.0,
    )
    v = assess(win, CFG)
    assert v.score == 0.0
    assert v.verdict_kind == "stalled"
    assert v.strongest_rule == "R5"


def test_assess_r6_repetitive_alone() -> None:
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
    win = LivenessWindow(
        seconds_since_output=0.0,
        cpu_ratio=0.0,
        io_bytes_per_s=CFG.io_kbps_active,
        child_events=0,
        output_novelty=1.0,
        voluntary_ctx_per_s=0.0,
        elapsed_s=10.0,
    )
    v = assess(win, CFG)
    assert v.score == pytest.approx(0.95)
    assert v.verdict_kind == "working"


def test_regression_pa002_counterexample_1_r1_io_dilution() -> None:
    win_base = LivenessWindow(
        seconds_since_output=0.0,
        cpu_ratio=0.0,
        io_bytes_per_s=0.0,
        child_events=0,
        output_novelty=1.0,
        voluntary_ctx_per_s=0.0,
        elapsed_s=10.0,
    )
    v_base = assess(win_base, CFG)
    assert v_base.score == pytest.approx(1.0)
    assert v_base.strongest_rule == "R1"
    assert v_base.verdict_kind == "working"

    win_raised = LivenessWindow(
        seconds_since_output=0.0,
        cpu_ratio=0.0,
        io_bytes_per_s=CFG.io_kbps_active,
        child_events=0,
        output_novelty=1.0,
        voluntary_ctx_per_s=0.0,
        elapsed_s=10.0,
    )
    v_raised = assess(win_raised, CFG)
    assert v_raised.score == pytest.approx(0.95)
    assert v_raised.strongest_rule == "R1"
    assert v_raised.verdict_kind == "working"
    assert v_raised.score < v_base.score


def test_regression_pa002_counterexample_2_evidence_window_gate_transition() -> None:
    win_30 = LivenessWindow(
        seconds_since_output=30.0,
        cpu_ratio=0.0,
        io_bytes_per_s=0.0,
        child_events=0,
        output_novelty=0.0,
        voluntary_ctx_per_s=0.0,
        elapsed_s=30.0,
    )
    v_30 = assess(win_30, CFG)
    assert v_30.score == pytest.approx(0.6)
    assert v_30.strongest_rule == "R1"

    win_29 = LivenessWindow(
        seconds_since_output=29.0,
        cpu_ratio=0.0,
        io_bytes_per_s=0.0,
        child_events=0,
        output_novelty=0.0,
        voluntary_ctx_per_s=0.0,
        elapsed_s=30.0,
    )
    v_29 = assess(win_29, CFG)
    assert v_29.score == pytest.approx(0.2 / 1.38)
    assert v_29.strongest_rule == "R6"
    assert v_29.score < v_30.score


def test_regression_pa002_counterexample_3_cpu_raise_r7_to_r4_boundary() -> None:
    win_r7 = LivenessWindow(
        seconds_since_output=90.0,
        cpu_ratio=0.0,
        io_bytes_per_s=0.0,
        child_events=0,
        output_novelty=None,
        voluntary_ctx_per_s=CFG.awake_per_s_active,
        elapsed_s=10.0,
    )
    v_r7 = assess(win_r7, CFG)
    assert v_r7.score == pytest.approx(0.55)
    assert v_r7.strongest_rule == "R7"

    cpu_mid = (CFG.cpu_busy_min + CFG.cpu_busy_max) / 2.0
    win_cpu = LivenessWindow(
        seconds_since_output=90.0,
        cpu_ratio=cpu_mid,
        io_bytes_per_s=0.0,
        child_events=0,
        output_novelty=None,
        voluntary_ctx_per_s=CFG.awake_per_s_active,
        elapsed_s=10.0,
    )
    v_cpu = assess(win_cpu, CFG)
    assert v_cpu.score == pytest.approx(0.575)
    assert v_cpu.strongest_rule == "R7"
    assert v_cpu.score >= v_r7.score


def test_assess_signals_dict_round_trips_window() -> None:
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


def test_stall_integrator_initial_pressure_is_zero() -> None:
    si = StallIntegrator(theta=CFG.stall_theta)
    assert si.pressure == 0.0
    assert si.should_kill(0.0) is True


def test_stall_integrator_step_returns_new_p() -> None:
    si = StallIntegrator(theta=CFG.stall_theta)
    p1 = si.step(verdict_score=0.0, dt=1.0)
    assert p1 == pytest.approx(1.0)
    assert si.pressure == pytest.approx(1.0)


def test_stall_integrator_monotonic_when_score_below_theta() -> None:
    si = StallIntegrator(theta=CFG.stall_theta)
    p = 0.0
    for _ in range(50):
        new_p = si.step(verdict_score=0.0, dt=1.0)
        assert new_p >= p
        p = new_p


def test_stall_integrator_drains_when_score_above_theta() -> None:
    si = StallIntegrator(theta=0.5)
    si.step(verdict_score=0.0, dt=10.0)
    assert si.pressure > 0.0
    for _ in range(5):
        si.step(verdict_score=1.0, dt=1.0)
    assert si.pressure < 10.0


def test_stall_integrator_pressure_never_negative() -> None:
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
    stall_window_s = 60.0
    dt = 1.0
    n_to_kill = int(stall_window_s)

    si = StallIntegrator(theta=CFG.stall_theta)

    for _ in range(n_to_kill - 1):
        si.step(verdict_score=0.0, dt=dt)
    assert si.pressure == pytest.approx(n_to_kill - 1)
    assert si.should_kill(stall_window_s) is False

    si.step(verdict_score=0.0, dt=dt)
    assert si.pressure == pytest.approx(n_to_kill)
    assert si.should_kill(stall_window_s) is True


def test_ev05_fresh_sparse_output_with_unknowns_is_working() -> None:
    win = LivenessWindow(
        seconds_since_output=0.0,
        cpu_ratio=None,
        io_bytes_per_s=None,
        child_events=None,
        output_novelty=None,
        voluntary_ctx_per_s=None,
        elapsed_s=10.0,
    )
    v = assess(win, CFG)
    assert v.score == 1.0
    assert v.verdict_kind == "working"
    assert v.strongest_rule == "R1"
    assert v.suspicious is False


def test_ev05_replacing_io_zero_with_unknown_does_not_raise_score() -> None:
    win_zero = LivenessWindow(
        seconds_since_output=90.0,
        cpu_ratio=0.05,
        io_bytes_per_s=0.0,
        child_events=0,
        output_novelty=0.5,
        voluntary_ctx_per_s=0.0,
        elapsed_s=90.0,
    )
    win_unknown = LivenessWindow(
        seconds_since_output=90.0,
        cpu_ratio=0.05,
        io_bytes_per_s=None,
        child_events=0,
        output_novelty=0.5,
        voluntary_ctx_per_s=0.0,
        elapsed_s=90.0,
    )
    v_zero = assess(win_zero, CFG)
    v_unknown = assess(win_unknown, CFG)
    assert v_zero.score == pytest.approx(v_unknown.score)
    assert v_zero.verdict_kind == v_unknown.verdict_kind
    assert v_unknown.score == pytest.approx(0.1)
