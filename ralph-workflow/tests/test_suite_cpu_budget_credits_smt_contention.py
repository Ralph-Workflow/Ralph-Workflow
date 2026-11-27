from __future__ import annotations

from ralph.verify_budget import SuiteBudgetReport

_CORES = 40
_WALL_BUDGET = 75.0
_CPU_BUDGET = 1800.0


def _report(*, wall: float, suite_cpu: float, foreign_cores: float) -> SuiteBudgetReport:
    return SuiteBudgetReport(
        wall_seconds=wall,
        suite_cpu_seconds=suite_cpu,
        host_busy_seconds=suite_cpu + foreign_cores * wall,
        cpu_count=_CORES,
        wall_budget_seconds=_WALL_BUDGET,
        cpu_budget_seconds=_CPU_BUDGET,
    )


def test_a_suite_cpu_overage_on_a_contended_host_is_host_contention_not_a_failure() -> None:
    report = _report(wall=60.0, suite_cpu=1809.3, foreign_cores=24.6)

    assert report.outcome == "host_contention"
    assert report.announce() is None


def test_a_suite_cpu_overage_on_a_quiet_host_still_fails() -> None:
    report = _report(wall=60.0, suite_cpu=1809.3, foreign_cores=0.5)

    assert report.outcome == "over_cpu_budget"
    assert report.announce() == "suite CPU exceeded"


def test_the_contention_credit_never_exceeds_half_the_cpu_budget() -> None:
    report = _report(wall=60.0, suite_cpu=_CPU_BUDGET * 1.51, foreign_cores=float(_CORES))

    assert report.outcome == "over_cpu_budget"
