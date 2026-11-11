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
    v4_m2 = _get_metric_float(v4, "M2")
    v5_m2 = _get_metric_float(v5, "M2")
    if v5_m2 > v4_m2 or bool(per_run and per_run.get("kernel_rule_regression", False)):
        return "KEEP_FALSE"

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

    if n_restore >= 1:
        return "KEEP_FALSE"

    v4_m8 = _get_metric_float(v4, "M8")
    v5_m8 = _get_metric_float(v5, "M8")
    time_ratio = (v5_m8 / v4_m8) if v4_m8 > 0.0 else 1.0

    v4_m9 = _get_metric_float(v4, "M9")
    v5_m9 = _get_metric_float(v5, "M9")
    token_ratio = (v5_m9 / v4_m9) if v4_m9 > 0.0 else 1.0

    cost_exceeded = (time_ratio > 1.25) or (token_ratio > 1.25)

    if n_improve >= 1:
        return "KEEP_TRUE_REDESIGN" if cost_exceeded else "KEEP_TRUE"

    if n_tie == 7 and not cost_exceeded:
        has_cost_improvement = (
            (v5_m8 < v4_m8 and token_ratio <= 1.0)
            or (v5_m9 < v4_m9 and time_ratio <= 1.0)
            or (v5_m8 < v4_m8 and v5_m9 < v4_m9)
        )
        if has_cost_improvement:
            return "KEEP_TRUE"

    return "KEEP_FALSE"


def test_metric_directions_higher_is_better() -> None:
    for m in ("M1", "M3", "M4", "M6"):
        assert per_metric_decision(1, 2, m) == "IMPROVE"
        assert per_metric_decision(2, 1, m) == "RESTORE"
        assert per_metric_decision(2, 2, m) == "KEEP_TIE"


def test_metric_directions_lower_is_better() -> None:
    for m in ("M2", "M5", "M7"):
        assert per_metric_decision(2, 1, m) == "IMPROVE"
        assert per_metric_decision(1, 2, m) == "RESTORE"
        assert per_metric_decision(2, 2, m) == "KEEP_TIE"


def test_per_metric_decision_raises_on_unknown_metric() -> None:
    with pytest.raises(ValueError, match="Unknown metric"):
        per_metric_decision(1, 2, "UNKNOWN")


def test_added_regression_vetoes_retention() -> None:
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

    v5_m5 = dict(base_v4, M5=0)
    assert decide(base_v4, v5_m5) == "KEEP_TRUE"

    v5_m7 = dict(base_v4, M7=0)
    assert decide(base_v4, v5_m7) == "KEEP_TRUE"

    v5_m5_worse = dict(base_v4, M5=3)
    assert decide(base_v4, v5_m5_worse) == "KEEP_FALSE"

    v5_m7_worse = dict(base_v4, M7=3)
    assert decide(base_v4, v5_m7_worse) == "KEEP_FALSE"


def test_equal_outcomes_with_lower_time_or_cost_handled_consistently() -> None:
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

    v5_faster = dict(v4, M8=15.0)
    assert decide(v4, v5_faster) == "KEEP_TRUE"

    v5_cheaper = dict(v4, M9=1400)
    assert decide(v4, v5_cheaper) == "KEEP_TRUE"

    v5_both_lower = dict(v4, M8=16.0, M9=1500)
    assert decide(v4, v5_both_lower) == "KEEP_TRUE"

    assert decide(v4, v4) == "KEEP_FALSE"

    v5_higher_time = dict(v4, M8=22.0)
    assert decide(v4, v5_higher_time) == "KEEP_FALSE"

    v5_excess_time = dict(v4, M8=26.0)
    assert decide(v4, v5_excess_time) == "KEEP_FALSE"


def test_secondary_cost_ratios_evaluated_independently() -> None:
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

    v5_time_spike = dict(v4, M1=2, M8=13.0, M9=500)
    assert decide(v4, v5_time_spike) == "KEEP_TRUE_REDESIGN"

    v5_token_spike = dict(v4, M1=2, M8=5.0, M9=1300)
    assert decide(v4, v5_token_spike) == "KEEP_TRUE_REDESIGN"

    v5_acceptable_cost = dict(v4, M1=2, M8=11.0, M9=1100)
    assert decide(v4, v5_acceptable_cost) == "KEEP_TRUE"

    v5_boundary_cost = dict(v4, M1=2, M8=12.5, M9=1250)
    assert decide(v4, v5_boundary_cost) == "KEEP_TRUE"
