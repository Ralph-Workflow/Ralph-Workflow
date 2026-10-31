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
from typing import TYPE_CHECKING, Protocol

import pytest

from ralph import verify as verify_module
from ralph import verify_budget as verify_budget_module
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
        {"test": (20.0, 700.0, 0.0), "test-verification-smoke": (10.0, 100.0, 0.0)},
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


# --- capacity-bound hosts ---------------------------------------------------


def _report_on(*, cores: int, wall: float, suite_cpu: float, host_busy: float) -> SuiteBudgetReport:
    return SuiteBudgetReport(
        wall_seconds=wall,
        suite_cpu_seconds=suite_cpu,
        host_busy_seconds=host_busy,
        cpu_count=cores,
        wall_budget_seconds=60.0,
        cpu_budget_seconds=600.0,
    )


def test_a_suite_that_saturates_a_small_host_is_capacity_bound_not_failed() -> None:
    # ~470 core-s of suite work on a 4-core CI runner needs >= 117.5 s of
    # wall clock even perfectly parallel; the suite kept 3.9 of 4 cores busy.
    report = _report_on(cores=4, wall=120.0, suite_cpu=470.0, host_busy=475.0)
    assert report.suite_utilisation == pytest.approx(470.0 / (4 * 120.0 - 5.0))
    assert report.outcome == "host_capacity"
    assert report.announce() is None


def test_a_wall_overrun_with_idle_capacity_left_still_fails() -> None:
    # 12 cores, 70 s wall, but the suite used only 300 core-s: 36 % of what
    # the host could have given it. The wall clock was not capacity-bound.
    report = _report_on(cores=12, wall=70.0, suite_cpu=300.0, host_busy=300.0)
    assert report.suite_utilisation == pytest.approx(300.0 / 840.0)
    assert report.outcome == "over_wall_budget"
    assert report.announce() == "wall clock exceeded even after crediting host contention"


def test_capacity_counts_only_the_cores_foreign_load_left_to_the_suite() -> None:
    # Foreign load held 2 of 4 cores for the whole run; the suite saturated
    # the other two. Contention credit alone (60 s) cannot explain 150 s,
    # but the suite left no capacity unused.
    report = _report_on(cores=4, wall=150.0, suite_cpu=290.0, host_busy=290.0 + 2 * 150.0)
    assert report.adjusted_wall_seconds > 60.0
    assert report.outcome == "host_capacity"


def test_capacity_rule_never_shelters_a_cpu_overrun() -> None:
    report = _report_on(cores=4, wall=170.0, suite_cpu=650.0, host_busy=660.0)
    assert report.outcome == "over_cpu_budget"


def test_describe_reports_the_suite_utilisation() -> None:
    text = _report_on(cores=4, wall=120.0, suite_cpu=470.0, host_busy=475.0).describe()
    assert "suite kept 99% of the free cores busy" in text


def test_run_verify_warns_but_passes_on_a_capacity_bound_host(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _FakeHost({"test": (100.0, 400.0, 0.0), "test-verification-smoke": (20.0, 70.0, 0.0)})
    monkeypatch.setattr(time, "monotonic", host.monotonic)
    monkeypatch.setattr(verify_module, "sample_suite_resources", host.sample)
    monkeypatch.setattr(verify_module.os, "cpu_count", lambda: 4)
    exit_code = verify_module.run_verify(cwd=tmp_path, runner=host)
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "WARNING" in captured.err
    assert "capacity" in captured.err
    assert "ACTION REQUIRED FOR AI AGENTS" not in captured.err


# --- orphaned descendants ---------------------------------------------------


class _FakeCpuTimes:
    def __init__(self, seconds: float) -> None:
        self.user = seconds
        self.system = 0.0


class _FakeProcess:
    """A process the tracker can observe: CPU, parent, session, liveness."""

    def __init__(self, pid: int, parent: int, session: int, cpu: float = 0.0) -> None:
        self.pid = pid
        self.parent = parent
        self.session = session
        self.cpu = cpu
        self.running = True

    def create_time(self) -> float:
        return float(self.pid)

    def cpu_times(self) -> _FakeCpuTimes:
        return _FakeCpuTimes(self.cpu)

    def ppid(self) -> int:
        return self.parent

    def is_running(self) -> bool:
        return self.running


class _OrphanTracker(Protocol):
    orphaned_cpu_seconds: float

    def sample(self, *, baseline: bool = False) -> None: ...


class _FakeHostProcesses:
    """The verify process (pid 1000, session 1) and every live process on the host."""

    pid = 1000

    def __init__(self) -> None:
        self.processes: list[_FakeProcess] = []

    def spawn(self, pid: int, parent: int, session: int, cpu: float = 0.0) -> _FakeProcess:
        process = _FakeProcess(pid, parent, session, cpu)
        self.processes.append(process)
        return process

    def children(self, *, recursive: bool) -> list[_FakeProcess]:
        assert recursive
        tree: list[_FakeProcess] = []
        parents = {self.pid}
        for process in self.processes:  # spawned parent-first
            if process.running and process.parent in parents:
                tree.append(process)
                parents.add(process.pid)
        return tree

    def session_of(self, pid: int) -> int | None:
        if pid == self.pid:
            return 1
        return next((p.session for p in self.processes if p.pid == pid and p.running), None)

    def session_members(self, sessions: frozenset[int]) -> list[_FakeProcess]:
        return [p for p in self.processes if p.running and p.session in sessions]

    def tracker(self) -> _OrphanTracker:
        return verify_budget_module.orphaned_cpu_tracker(
            self, session_of=self.session_of, session_members=self.session_members
        )


def test_cpu_of_a_descendant_orphaned_during_the_step_is_charged_to_the_suite() -> None:
    host = _FakeHostProcesses()
    make = host.spawn(2000, parent=host.pid, session=2000)
    tracker = host.tracker()
    tracker.sample()
    daemon = host.spawn(2001, parent=make.pid, session=2000, cpu=0.5)
    tracker.sample()
    # ``make`` exits; its background child is re-parented to init and keeps
    # burning CPU that ``getrusage(RUSAGE_CHILDREN)`` will never see.
    make.running = False
    daemon.parent = 1
    daemon.cpu = 2.0
    tracker.sample()
    daemon.cpu = 3.0
    tracker.sample()
    daemon.running = False
    tracker.sample()

    assert tracker.orphaned_cpu_seconds == pytest.approx(3.0)


def test_a_descendant_orphaned_before_any_sample_saw_it_is_found_by_its_session() -> None:
    host = _FakeHostProcesses()
    host.spawn(2000, parent=host.pid, session=2000)
    tracker = host.tracker()
    tracker.sample()
    # ``sh -c 'burn & exit'``: the shell is gone before the next sample, so
    # the burner was never seen inside the tree -- only in the step's session.
    host.spawn(2002, parent=1, session=2000, cpu=1.5)
    unrelated = host.spawn(3000, parent=1, session=3000, cpu=9.0)
    tracker.sample()

    assert unrelated.cpu == 9.0
    assert tracker.orphaned_cpu_seconds == pytest.approx(1.5)


def test_descendants_reaped_inside_the_tree_are_left_to_rusage() -> None:
    host = _FakeHostProcesses()
    child = host.spawn(2000, parent=host.pid, session=2000, cpu=1.0)
    tracker = host.tracker()
    tracker.sample()
    child.cpu = 4.0
    tracker.sample()
    child.running = False
    tracker.sample()

    assert tracker.orphaned_cpu_seconds == 0.0


def test_an_orphan_older_than_the_step_is_charged_only_for_the_step() -> None:
    host = _FakeHostProcesses()
    worker = host.spawn(2000, parent=host.pid, session=2000, cpu=10.0)
    tracker = host.tracker()
    tracker.sample(baseline=True)
    worker.parent = 1
    worker.cpu = 12.5
    tracker.sample()

    assert tracker.orphaned_cpu_seconds == pytest.approx(2.5)


def test_processes_sharing_the_verify_session_are_never_charged() -> None:
    host = _FakeHostProcesses()
    host.spawn(2000, parent=host.pid, session=1)
    tracker = host.tracker()
    tracker.sample()
    host.spawn(2001, parent=1, session=1, cpu=5.0)
    tracker.sample()

    assert tracker.orphaned_cpu_seconds == 0.0


def test_run_verify_charges_orphaned_descendant_cpu_to_the_suite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 300 core-s of suite CPU through rusage plus 500 core-s burnt by an
    # orphan: over the 700 core-s CPU budget, though rusage alone is not.
    host = _FakeHost({"test": (40.0, 300.0, 500.0), "test-verification-smoke": (10.0, 0.0, 0.0)})
    monkeypatch.setattr(time, "monotonic", host.monotonic)
    monkeypatch.setattr(verify_module, "sample_suite_resources", host.sample)
    monkeypatch.setattr(verify_module.os, "cpu_count", lambda: _CORES)
    orphaned = iter((500.0, 0.0))

    class _Tracker:
        orphaned_cpu_seconds = 0.0

        def __enter__(self) -> _Tracker:
            return self

        def __exit__(self, *exc_info: object) -> None:
            self.orphaned_cpu_seconds = next(orphaned)

    exit_code = verify_module.run_verify(cwd=tmp_path, runner=host, orphan_tracker=_Tracker)
    captured = capsys.readouterr()
    assert exit_code == TIMEOUT_EXIT_CODE
    assert "suite CPU 800.0 / 700.0 core-s" in captured.out
    assert "suite CPU exceeded" in captured.err
