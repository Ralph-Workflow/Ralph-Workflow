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
        cpu_ratio=CFG.cpu_busy_min - 0.01,  # KNOWN idle   => CpuBusy=0
        io_bytes_per_s=CFG.io_kbps_inactive - 1.0,  # KNOWN idle   => IoActive=0
        child_events=0,  # KNOWN idle   => TreeActive=0
        output_novelty=CFG.repetitive_min + 0.1,  # KNOWN novel  => Repetitive=0
        voluntary_ctx_per_s=CFG.awake_per_s_inactive - 0.1,  # KNOWN asleep => Awake=0
        elapsed_s=10.0,
    )
    v = assess(win, CFG)
    assert v.score == 0.0
    assert v.verdict_kind == "stalled"
    assert v.strongest_rule == "R5"


def test_assess_r6_repetitive_alone() -> None:
    """Rule 6 (repetitive) fires when Repetitive=1, others not active.

    To isolate R6 from R5, set seconds_since_output=0 (OutputRecent=1, which
    forces NOT OutputRecent=0 and so disables R5). The tree signal is also
    known-zero so R3 doesn't fire.

    R6 fires with canonical z=0.2 (PRODUCT_CRITERIA Part A / D2).
    R1 is suppressed by Repetitive=1 (NOT Repetitive=0).
    With R6 firing alone, Sugeno score is 0.2.
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
    """R1 + R2 both firing (recent + I/O): literal Sugeno weighted-average.

    PRODUCT_CRITERIA Part A defines the score as ``s = Σwᵢzᵢ / Σwᵢ``.
    R1 fires (w₁=1, z₁=1.0) and R2 fires (w₂=1, z₂=0.9); no other rule
    fires. ``Σw = 2`` and ``Σw·z = 1.0 + 0.9 = 1.9``, so ``s = 0.95``.
    This is the verbatim weighted-average PRODUCT_CRITERIA Part A prescribes.
    """
    win = LivenessWindow(
        seconds_since_output=0.0,
        cpu_ratio=0.0,  # KNOWN-not-busy so R4 doesn't fire on cpu alone
        io_bytes_per_s=CFG.io_kbps_active,
        child_events=0,  # KNOWN no children so R3 doesn't fire
        output_novelty=1.0,  # KNOWN novel so R1's NOT Repetitive is 1
        voluntary_ctx_per_s=0.0,  # KNOWN asleep so R7 doesn't fire
        elapsed_s=10.0,
    )
    v = assess(win, CFG)
    assert v.score == pytest.approx(0.95)
    assert v.verdict_kind == "working"


# ---------------------------------------------------------------------------
# PA-002 exact-semantics regressions (counterexample traces locked)
# ---------------------------------------------------------------------------


def test_regression_pa002_counterexample_1_r1_io_dilution() -> None:
    """PA-002 Counterexample 1: literal-Sugeno weighted average dilution.

    An R1-fired window scores 1.0; raising working signal ``io`` to active
    (64 KiB/s) fires R2 (z₂=0.9), diluting the score to 0.95 (1.0 -> 0.95).
    This proves that global multi-signal and per-signal monotonicity do not
    hold unconditionally under the shipped weighted average, locking the
    exact scores as intentional, documented behavior.
    """
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
    """PA-002 Counterexample 2: evidence-window gate transition (30s -> 29s).

    Improving output recency 30s -> 29s with repetitive output (novelty 0.0)
    crosses the evidence-window gate (``evidence_window_s = 30.0``):
    - At 30s: output is at/outside the window; novelty is gate-unknown (None),
      so ``Repetitive``=0.0. R1 fires on recency (0.6, z=1.0) and R5 fires (0.4,
      z=0.0). Score = 0.6 / 1.0 = 0.6.
    - At 29s: output is inside the window; novelty is evaluated as repetitive,
      so ``Repetitive``=1.0. R1 is blocked (0.0), R6 fires (1.0, z=0.2), and
      R5 fires (0.38, z=0.0). Score = 0.2 / 1.38 ≈ 0.1449.

    Improving recency drops the score 0.6 -> 0.145 because entering the
    evidence window activates R6 loop detection. This is the spec-internal
    contradiction that defeats every candidate aggregate.
    """
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
    """PA-002 Counterexample 3: R7 -> R4 CPU-raise boundary under literal Sugeno.

    At base (awake=1.0, cpu=0.0), R7 fires alone with z=0.55 -> score = 0.55.
    Raising CPU membership to 0.5 activates R4 (w4=0.5, z=0.6) and deflates
    R7 via ``NOT CpuBusy`` (w7=0.5, z=0.55).
    - Under literal-Sugeno weighted average:
      Score = (0.5 * 0.6 + 0.5 * 0.55) / (0.5 + 0.5) = 0.575.
      The score is monotone non-decreasing (0.55 -> 0.575).
    - Under strongest-product (max_i w_i * z_i):
      Score would drop from 0.55 to max(0.5*0.6, 0.5*0.55) = 0.30.

    This locks why the shipped literal-Sugeno aggregate survives the
    negated-antecedent transition that rules out strongest-product.
    """
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


# ---------------------------------------------------------------------------
# EV-05 regression tests: D5 unknown signal semantics
# ---------------------------------------------------------------------------


def test_ev05_fresh_sparse_output_with_unknowns_is_working() -> None:
    """EV-05: fresh sparse output with all non-output signals unknown fires R1.

    When output was just produced (seconds_since_output=0.0) but novelty is
    None (<20 lines), R1 fires alone with score 1.0 (working). It must NOT
    produce 0.0 unknown.
    """
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
    """EV-05: replacing I/O zero with unknown does not change score vs zero.

    Replacing a known-zero signal with unknown (None) must never extend a
    deadline or raise score vs zero (D5 fail-closed).
    """
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
