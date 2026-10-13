"""Tests and executable specification for the policy v5 paired-replay decision table.

Implements the scoring rubric and keep/restore decision algorithm specified in
``ralph-workflow/docs/plans/policy-v5-paired-replay-plan.md``:
- M1, M3, M4, M6: higher is better.
- M2 (regressions), M5 (duplicate tests), M7 (human rework): lower is better.
- Protected regressions (M2): ANY increase in regressions (v5.M2 > v4.M2) MUST veto
  retention and return KEEP_FALSE.
- Secondary costs (M8 elapsed seconds, M9 token count): evaluated independently as
  ratios; redesign if either exceeds 1.25x. Never sum seconds and tokens.
- Equal primary outcomes with lower time/cost handled consistently.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

HIGHER_IS_BETTER: frozenset[str] = frozenset({"M1", "M3", "M4", "M6"})
LOWER_IS_BETTER: frozenset[str] = frozenset({"M2", "M5", "M7", "M8", "M9"})
PRIMARY_METRICS: tuple[str, ...] = ("M1", "M2", "M3", "M4", "M5", "M6", "M7")


def per_metric_decision(
    v4_val: float | int,
    v5_val: float | int,
    metric_name: str,
) -> str:
    """Evaluate per-metric direction between v4 baseline and v5 treatment.

    For higher = better (M1, M3, M4, M6):
        v5 > v4 -> IMPROVE
        v5 < v4 -> RESTORE
        v5 == v4 -> KEEP_TIE (TIE)

    For lower = better (M2, M5, M7, M8, M9):
        v5 < v4 -> IMPROVE
        v5 > v4 -> RESTORE
        v5 == v4 -> KEEP_TIE (TIE)
    """
    if metric_name in HIGHER_IS_BETTER:
        if v5_val > v4_val:
            return "IMPROVE"
        if v5_val < v4_val:
            return "RESTORE"
        return "KEEP_TIE"

    if metric_name in LOWER_IS_BETTER:
        if v5_val < v4_val:
            return "IMPROVE"
        if v5_val > v4_val:
            return "RESTORE"
        return "KEEP_TIE"

    raise ValueError(f"Unknown metric: {metric_name}")


def _get_metric_float(metrics: Mapping[str, float | int], key: str, default: float = 0.0) -> float:
    raw = metrics.get(key, default)
    return float(raw)


def decide(
    v4: Mapping[str, float | int],
    v5: Mapping[str, float | int],
    per_run: Mapping[str, object] | None = None,
) -> str:
    """Decide keep / restore / redesign for a paired replay comparison.

    Parameters
    ----------
    v4:
        Baseline metrics dictionary containing M1-M9.
    v5:
        Treatment metrics dictionary containing M1-M9.
    per_run:
        Optional per-run context dict (e.g. kernel_rule_regression flag).

    Returns
    -------
    str:
        "KEEP_FALSE", "KEEP_TRUE", or "KEEP_TRUE_REDESIGN".
    """
    # 1. Protected regressions (M2): ANY increase in regressions (v5.M2 > v4.M2)
    # MUST veto retention and return KEEP_FALSE.
    v4_m2 = _get_metric_float(v4, "M2")
    v5_m2 = _get_metric_float(v5, "M2")
    if v5_m2 > v4_m2 or bool(per_run and per_run.get("kernel_rule_regression", False)):
        return "KEEP_FALSE"

    # 2. Evaluate primary metrics (M1-M7)
    n_restore = 0
    n_improve = 0
    n_tie = 0

    for m in PRIMARY_METRICS:
        val4 = _get_metric_float(v4, m)
        val5 = _get_metric_float(v5, m)
        res = per_metric_decision(val4, val5, m)
        if res == "RESTORE":
            n_restore += 1
        elif res == "IMPROVE":
            n_improve += 1
        else:
            n_tie += 1

    # Any regression on the primary axis vetoes retention
    if n_restore >= 1:
        return "KEEP_FALSE"

    # 3. Dimensional cost comparisons: M8 is seconds, M9 is tokens.
    # Evaluated independently; do NOT sum seconds and tokens together.
    v4_m8 = _get_metric_float(v4, "M8")
    v5_m8 = _get_metric_float(v5, "M8")
    time_ratio = (v5_m8 / v4_m8) if v4_m8 > 0.0 else 1.0

    v4_m9 = _get_metric_float(v4, "M9")
    v5_m9 = _get_metric_float(v5, "M9")
    token_ratio = (v5_m9 / v4_m9) if v4_m9 > 0.0 else 1.0

    # Redesign if either dimension exceeds 1.25x
    cost_exceeded = (time_ratio > 1.25) or (token_ratio > 1.25)

    # 4. Resolve decision
    if n_improve >= 1:
        return "KEEP_TRUE_REDESIGN" if cost_exceeded else "KEEP_TRUE"

    # No primary metric improved (n_improve == 0, n_tie == 7)
    if n_tie == 7 and not cost_exceeded:
        # Equal primary outcomes with lower time/cost handled consistently
        has_cost_improvement = (
            (v5_m8 < v4_m8 and token_ratio <= 1.0)
            or (v5_m9 < v4_m9 and time_ratio <= 1.0)
            or (v5_m8 < v4_m8 and v5_m9 < v4_m9)
        )
        if has_cost_improvement:
            return "KEEP_TRUE"

    return "KEEP_FALSE"


# =====================================================================
# Tests for per_metric_decision and decide
# =====================================================================


def test_metric_directions_higher_is_better() -> None:
    """M1, M3, M4, M6 are higher = better."""
    for m in ("M1", "M3", "M4", "M6"):
        assert per_metric_decision(1, 2, m) == "IMPROVE"
        assert per_metric_decision(2, 1, m) == "RESTORE"
        assert per_metric_decision(2, 2, m) == "KEEP_TIE"


def test_metric_directions_lower_is_better() -> None:
    """M2 (regressions), M5 (duplicate tests), M7 (human rework) are lower = better."""
    for m in ("M2", "M5", "M7"):
        assert per_metric_decision(2, 1, m) == "IMPROVE"
        assert per_metric_decision(1, 2, m) == "RESTORE"
        assert per_metric_decision(2, 2, m) == "KEEP_TIE"


def test_per_metric_decision_raises_on_unknown_metric() -> None:
    """Unknown metric name raises ValueError."""
    with pytest.raises(ValueError, match="Unknown metric"):
        per_metric_decision(1, 2, "UNKNOWN")


def test_added_regression_vetoes_retention() -> None:
    """Any increase in regressions (v5.M2 > v4.M2) MUST veto retention and return KEEP_FALSE."""
    v4: dict[str, float | int] = {
        "M1": 2,
        "M2": 0,
        "M3": 2,
        "M4": 2,
        "M5": 0,
        "M6": 2,
        "M7": 0,
        "M8": 10.0,
        "M9": 100,
    }
    # Even if all other metrics improve to maximum, M2 increase must veto
    v5: dict[str, float | int] = {
        "M1": 3,
        "M2": 1,
        "M3": 3,
        "M4": 3,
        "M5": 0,
        "M6": 3,
        "M7": 0,
        "M8": 5.0,
        "M9": 50,
    }
    assert decide(v4, v5) == "KEEP_FALSE"


def test_kernel_rule_regression_vetoes_retention() -> None:
    """Kernel rule regression flag in per_run forces KEEP_FALSE."""
    v4: dict[str, float | int] = {
        "M1": 2,
        "M2": 0,
        "M3": 2,
        "M4": 2,
        "M5": 0,
        "M6": 2,
        "M7": 0,
        "M8": 10.0,
        "M9": 100,
    }
    v5: dict[str, float | int] = {
        "M1": 3,
        "M2": 0,
        "M3": 2,
        "M4": 2,
        "M5": 0,
        "M6": 2,
        "M7": 0,
        "M8": 10.0,
        "M9": 100,
    }
    assert decide(v4, v5, per_run={"kernel_rule_regression": True}) == "KEEP_FALSE"


def test_fewer_duplicates_and_rework_improve() -> None:
    """Fewer duplicates (M5) or rework (M7) is an IMPROVE and yields KEEP_TRUE."""
    base_v4: dict[str, float | int] = {
        "M1": 2,
        "M2": 0,
        "M3": 2,
        "M4": 2,
        "M5": 2,
        "M7": 2,
        "M6": 2,
        "M8": 10.0,
        "M9": 100,
    }

    # v5 reduces duplicate tests from 2 to 0
    v5_m5 = dict(base_v4, M5=0)
    assert decide(base_v4, v5_m5) == "KEEP_TRUE"

    # v5 reduces human rework from 2 to 0
    v5_m7 = dict(base_v4, M7=0)
    assert decide(base_v4, v5_m7) == "KEEP_TRUE"

    # More duplicates or rework triggers RESTORE and returns KEEP_FALSE
    v5_m5_worse = dict(base_v4, M5=3)
    assert decide(base_v4, v5_m5_worse) == "KEEP_FALSE"

    v5_m7_worse = dict(base_v4, M7=3)
    assert decide(base_v4, v5_m7_worse) == "KEEP_FALSE"


def test_equal_outcomes_with_lower_time_or_cost_handled_consistently() -> None:
    """Equal primary outcomes (n_tie == 7) with lower time or cost is kept consistently."""
    v4: dict[str, float | int] = {
        "M1": 2,
        "M2": 0,
        "M3": 2,
        "M4": 2,
        "M5": 0,
        "M6": 2,
        "M7": 0,
        "M8": 20.0,
        "M9": 2000,
    }

    # Case A: Same primary quality, but 25% faster (M8 15.0s vs 20.0s), tokens tied
    v5_faster = dict(v4, M8=15.0)
    assert decide(v4, v5_faster) == "KEEP_TRUE"

    # Case B: Same primary quality, but 30% fewer tokens (M9 1400 vs 2000), time tied
    v5_cheaper = dict(v4, M9=1400)
    assert decide(v4, v5_cheaper) == "KEEP_TRUE"

    # Case C: Both time and tokens lower
    v5_both_lower = dict(v4, M8=16.0, M9=1500)
    assert decide(v4, v5_both_lower) == "KEEP_TRUE"

    # Case D: Equal primary quality with identical time and tokens -> no improvement
    assert decide(v4, v4) == "KEEP_FALSE"

    # Case E: Equal primary quality with higher time or tokens (no primary improvement)
    v5_higher_time = dict(v4, M8=22.0)
    assert decide(v4, v5_higher_time) == "KEEP_FALSE"

    # Case F: Equal primary quality with time exceeding 1.25x -> KEEP_FALSE
    v5_excess_time = dict(v4, M8=26.0)
    assert decide(v4, v5_excess_time) == "KEEP_FALSE"


def test_secondary_cost_ratios_evaluated_independently() -> None:
    """Secondary cost ratios (M8 seconds, M9 tokens) are evaluated independently, not summed.

    If either ratio exceeds 1.25x, redesign is required. Seconds and tokens are never added.
    """
    v4: dict[str, float | int] = {
        "M1": 1,
        "M2": 0,
        "M3": 2,
        "M4": 2,
        "M5": 0,
        "M6": 2,
        "M7": 0,
        "M8": 10.0,
        "M9": 1000,
    }

    # v5 improves M1 (correctness: 1 -> 2)
    # Dimension 1 exceeds: time ratio is 1.30x (> 1.25), while token ratio is 0.5x (< 1.0)
    # If summed naively (13 + 500 = 513 vs 10 + 1000 = 1010), ratio would be 0.51x and miss the time spike!
    v5_time_spike = dict(v4, M1=2, M8=13.0, M9=500)
    assert decide(v4, v5_time_spike) == "KEEP_TRUE_REDESIGN"

    # Dimension 2 exceeds: token ratio is 1.30x (> 1.25), while time ratio is 0.5x (< 1.0)
    # If summed naively, the token scale would dominate. Evaluated independently, redesign is triggered.
    v5_token_spike = dict(v4, M1=2, M8=5.0, M9=1300)
    assert decide(v4, v5_token_spike) == "KEEP_TRUE_REDESIGN"

    # Neither exceeds (time 1.1x, tokens 1.1x) with primary improvement -> KEEP_TRUE
    v5_acceptable_cost = dict(v4, M1=2, M8=11.0, M9=1100)
    assert decide(v4, v5_acceptable_cost) == "KEEP_TRUE"

    # Exactly 1.25x boundary (does not exceed 1.25x) -> KEEP_TRUE
    v5_boundary_cost = dict(v4, M1=2, M8=12.5, M9=1250)
    assert decide(v4, v5_boundary_cost) == "KEEP_TRUE"
