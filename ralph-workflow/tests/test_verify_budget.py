"""The combined test budget is judged net of host contention.

Wall clock on a shared host measures every process that ran, not only the
suite. ``ralph.verify_budget.SuiteBudgetReport`` charges the suite for the
CPU its own process trees used (a hard limit) and for wall clock net of the
CPU other processes took (a WARNING when only contention pushed it over,
a failure otherwise). These tests drive both the report and ``run_verify``
with injected clocks and resource samples, so they never depend on how
busy the machine running them is.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from ralph import verify as verify_module
from ralph.executor.process import TIMEOUT_EXIT_CODE, ProcessResult
from ralph.verify_budget import SuiteBudgetReport

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

_CORES = 12


def _report(*, wall: float, suite_cpu: float, host_busy: float) -> SuiteBudgetReport:
    return SuiteBudgetReport(
        wall_seconds=wall,
        suite_cpu_seconds=suite_cpu,
        host_busy_seconds=host_busy,
        cpu_count=_CORES,
        wall_budget_seconds=60.0,
        cpu_budget_seconds=600.0,
    )


def test_a_run_inside_both_budgets_is_within_budget() -> None:
    assert _report(wall=50.0, suite_cpu=400.0, host_busy=450.0).outcome == "within_budget"


def test_a_wall_overrun_caused_by_other_processes_is_only_a_warning() -> None:
    # Other processes kept 4 of 12 cores busy for the whole 66 s: at most
    # 22 s of wall time was theirs, so the suite alone needed ~44 s.
    report = _report(wall=66.0, suite_cpu=450.0, host_busy=450.0 + 4 * 66.0)
    assert report.contention_credit_seconds == pytest.approx(22.0)
    assert report.adjusted_wall_seconds == pytest.approx(44.0)
    assert report.outcome == "host_contention"
    assert report.announce() is None


def test_a_wall_overrun_on_an_idle_host_fails() -> None:
    report = _report(wall=66.0, suite_cpu=450.0, host_busy=450.0)
    assert report.outcome == "over_wall_budget"
    assert report.announce() == "wall clock exceeded even after crediting host contention"


def test_contention_credit_never_exceeds_what_other_processes_used() -> None:
    # Slightly under-reported host time must not turn into negative credit.
    report = _report(wall=70.0, suite_cpu=450.0, host_busy=440.0)
    assert report.foreign_cpu_seconds == 0.0
    assert report.outcome == "over_wall_budget"


def test_the_cpu_budget_fails_however_fast_the_wall_clock_was() -> None:
    report = _report(wall=30.0, suite_cpu=700.0, host_busy=720.0)
    assert report.outcome == "over_cpu_budget"
    assert report.announce() == "suite CPU exceeded"


def test_describe_reports_every_number_the_verdict_used() -> None:
    text = _report(wall=66.0, suite_cpu=450.0, host_busy=450.0 + 4 * 66.0).describe()
    assert "wall 66.00s / 60.0s" in text
    assert "contention-adjusted 44.00s" in text
    assert "4.0 of 12 cores busy" in text
    assert "suite CPU 450.0 / 600.0 core-s" in text


# --- run_verify integration -------------------------------------------------


class _FakeHost:
    """A clock and CPU counters that advance only when a test step "runs".

    ``run_verify`` reads wall clock through ``time.monotonic`` and resources
    through ``sample_suite_resources``; the stub runner advances both by what
    each ``make`` target is scripted to cost, so the real step list is driven
    without any real process, sleep or dependence on this machine's load.
    """

    def __init__(self, costs: dict[str, tuple[float, float, float]]) -> None:
        # target -> (wall seconds, suite CPU core-s, foreign CPU core-s)
        self.costs = costs
        self.now = 0.0
        self.suite_cpu = 0.0
        self.host_busy = 0.0
        self.timeouts: dict[str, float | None] = {}

    def monotonic(self) -> float:
        return self.now

    def sample(self) -> tuple[float, float]:
        return self.suite_cpu, self.host_busy

    def __call__(
        self,
        command: str,
        args: Sequence[str] = (),
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        capture_output: bool = True,
    ) -> ProcessResult:
        del cwd, env, capture_output
        target = args[0] if command == "make" and args else ""
        if target in self.costs:
            self.timeouts[target] = timeout
            wall, suite_cpu, foreign_cpu = self.costs[target]
            self.now += wall
            self.suite_cpu += suite_cpu
            self.host_busy += suite_cpu + foreign_cpu
        return ProcessResult(command=(command, *args), returncode=0, stdout="", stderr="")


def _run_verify(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    costs: dict[str, tuple[float, float, float]],
) -> tuple[int, _FakeHost]:
    host = _FakeHost(costs)
    monkeypatch.setattr(time, "monotonic", host.monotonic)
    monkeypatch.setattr(verify_module, "sample_suite_resources", host.sample)
    monkeypatch.setattr(verify_module.os, "cpu_count", lambda: _CORES)
    return verify_module.run_verify(cwd=tmp_path, runner=host), host


def test_run_verify_warns_but_passes_when_contention_explains_the_overrun(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 40 s + 26 s = 66 s while other processes kept 4 of 12 cores busy.
    exit_code, _host = _run_verify(
        monkeypatch,
        tmp_path,
        {"test": (40.0, 400.0, 160.0), "test-verification-smoke": (26.0, 50.0, 104.0)},
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "WARNING" in captured.err
    assert "contention-adjusted 44.00s" in captured.out
    assert "ACTION REQUIRED FOR AI AGENTS" not in captured.err


def test_run_verify_fails_an_overrun_the_host_cannot_explain(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    exit_code, _host = _run_verify(
        monkeypatch,
        tmp_path,
        {"test": (40.0, 400.0, 0.0), "test-verification-smoke": (26.0, 50.0, 0.0)},
    )
    captured = capsys.readouterr()
    assert exit_code == TIMEOUT_EXIT_CODE
    assert "wall clock exceeded even after crediting host contention" in captured.err


def test_run_verify_fails_when_the_suite_exceeds_its_cpu_budget(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    exit_code, _host = _run_verify(
        monkeypatch,
        tmp_path,
        {"test": (20.0, 600.0, 0.0), "test-verification-smoke": (10.0, 100.0, 0.0)},
    )
    captured = capsys.readouterr()
    assert exit_code == TIMEOUT_EXIT_CODE
    assert "suite CPU exceeded" in captured.err


def test_run_verify_gives_tracked_steps_only_the_remaining_hang_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exit_code, host = _run_verify(
        monkeypatch,
        tmp_path,
        {"test": (30.0, 300.0, 0.0), "test-verification-smoke": (10.0, 50.0, 0.0)},
    )
    assert exit_code == 0
    assert host.timeouts == {"test": 180.0, "test-verification-smoke": 150.0}


def test_run_verify_stops_once_the_hang_cap_is_spent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    exit_code, host = _run_verify(
        monkeypatch,
        tmp_path,
        {"test": (181.0, 300.0, 0.0), "test-verification-smoke": (10.0, 50.0, 0.0)},
    )
    captured = capsys.readouterr()
    assert exit_code == TIMEOUT_EXIT_CODE
    assert "test-verification-smoke" not in host.timeouts, (
        "the smoke step must not start once the hang cap is spent"
    )
    assert "budget exhausted — cumulative test time exceeded" in captured.err
