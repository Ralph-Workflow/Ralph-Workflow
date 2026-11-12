from __future__ import annotations

import os
import resource
import sys
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import psutil

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from types import TracebackType
    from typing import Protocol

    class _CpuTimes(Protocol):
        @property
        def user(self) -> float: ...
        @property
        def system(self) -> float: ...

    class _Process(Protocol):
        @property
        def pid(self) -> int: ...
        def create_time(self) -> float: ...
        def cpu_times(self) -> _CpuTimes: ...
        def ppid(self) -> int: ...
        def is_running(self) -> bool: ...

    class _ProcessTree(Protocol):
        @property
        def pid(self) -> int: ...
        def children(self, *, recursive: bool) -> Sequence[_Process]: ...


CAPACITY_SATURATION = 0.9

SuiteBudgetOutcome = Literal[
    "within_budget",
    "host_contention",
    "host_capacity",
    "over_wall_budget",
    "over_cpu_budget",
]


def sample_suite_resources() -> tuple[float, float]:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    host = psutil.cpu_times()
    return usage.ru_utime + usage.ru_stime, host.user + host.system


_ORPHAN_SAMPLE_INTERVAL_SECONDS = 0.5


@dataclass(slots=True)
class _Descendant:
    process: _Process
    baseline_cpu: float
    cpu: float
    orphaned: bool = False


class _OrphanedCpuTracker:
    def __init__(
        self,
        root: _ProcessTree,
        interval: float,
        session_of: Callable[[int], int | None],
        session_members: Callable[[frozenset[int]], Sequence[_Process]],
    ) -> None:
        self._root = root
        self._interval = interval
        self._session_of = session_of
        self._session_members = session_members
        self._sessions: set[int] = set()
        self._descendants: dict[tuple[int, float], _Descendant] = {}
        self._finished_orphan_cpu = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._sample_until_stopped, name="ralph-verify-orphan-cpu", daemon=True
        )
        self.orphaned_cpu_seconds = 0.0

    def __enter__(self) -> _OrphanedCpuTracker:
        self.sample(baseline=True)
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stop.set()
        self._thread.join(timeout=self._interval + 1.0)
        self.sample()

    def _sample_until_stopped(self) -> None:
        while not self._stop.wait(self._interval):
            self.sample()

    def sample(self, *, baseline: bool = False) -> None:
        try:
            in_tree = list(self._root.children(recursive=True))
        except psutil.Error:
            return
        tree_pids = {self._root.pid} | {process.pid for process in in_tree}
        own_session = self._session_of(self._root.pid)
        for process in in_tree:
            session = self._session_of(process.pid)
            if session is not None and session != own_session:
                self._sessions.add(session)
        strays = [
            process
            for process in (
                self._session_members(frozenset(self._sessions)) if self._sessions else ()
            )
            if process.pid not in tree_pids
        ]
        seen: set[tuple[int, float]] = set()
        for process, orphaned in [(p, False) for p in in_tree] + [(p, True) for p in strays]:
            observed = _observe(process)
            if observed is None:
                continue
            key, cpu = observed
            seen.add(key)
            known = self._descendants.get(key)
            if known is None:
                known = _Descendant(process, cpu if baseline else 0.0, cpu)
                self._descendants[key] = known
            known.cpu = max(known.cpu, cpu)
            known.orphaned = known.orphaned or orphaned
        for key, known in list(self._descendants.items()):
            if key not in seen:
                self._follow_departed(key, known, tree_pids)
        self.orphaned_cpu_seconds = self._finished_orphan_cpu + sum(
            known.cpu - known.baseline_cpu for known in self._descendants.values() if known.orphaned
        )

    def _follow_departed(
        self, key: tuple[int, float], known: _Descendant, tree_pids: set[int]
    ) -> None:
        try:
            running = known.process.is_running()
            parent = known.process.ppid() if running else None
            cpu = _cpu_seconds(known.process) if running else None
        except psutil.Error:
            running, parent, cpu = False, None, None
        if parent is not None and parent in tree_pids:
            return
        if running and cpu is not None:
            known.orphaned = True
            known.cpu = max(known.cpu, cpu)
            return
        if known.orphaned:
            self._finished_orphan_cpu += known.cpu - known.baseline_cpu
        del self._descendants[key]


def _cpu_seconds(process: _Process) -> float:
    times = process.cpu_times()
    return times.user + times.system


def _observe(process: _Process) -> tuple[tuple[int, float], float] | None:
    try:
        return (process.pid, process.create_time()), _cpu_seconds(process)
    except psutil.Error:
        return None


def _host_session_of(pid: int) -> int | None:
    try:
        return os.getsid(pid)
    except OSError:
        return None


def _host_session_members(sessions: frozenset[int]) -> list[_Process]:
    members: list[_Process] = []
    for pid in psutil.pids():
        if _host_session_of(pid) in sessions:
            try:
                members.append(psutil.Process(pid))
            except psutil.Error:
                continue
    return members


def orphaned_cpu_tracker(
    root: _ProcessTree | None = None,
    *,
    interval: float = _ORPHAN_SAMPLE_INTERVAL_SECONDS,
    session_of: Callable[[int], int | None] = _host_session_of,
    session_members: Callable[[frozenset[int]], Sequence[_Process]] = _host_session_members,
) -> _OrphanedCpuTracker:
    return _OrphanedCpuTracker(
        root if root is not None else psutil.Process(os.getpid()),
        interval,
        session_of,
        session_members,
    )


@dataclass(frozen=True, slots=True)
class SuiteBudgetReport:
    wall_seconds: float
    suite_cpu_seconds: float
    host_busy_seconds: float
    cpu_count: int
    wall_budget_seconds: float
    cpu_budget_seconds: float

    @property
    def foreign_cpu_seconds(self) -> float:
        return max(0.0, self.host_busy_seconds - self.suite_cpu_seconds)

    @property
    def contention_credit_seconds(self) -> float:
        return self.foreign_cpu_seconds / max(1, self.cpu_count)

    @property
    def adjusted_wall_seconds(self) -> float:
        return max(0.0, self.wall_seconds - self.contention_credit_seconds)

    @property
    def suite_utilisation(self) -> float:
        free_core_seconds = self.wall_seconds * max(1, self.cpu_count) - self.foreign_cpu_seconds
        if free_core_seconds <= 0.0:
            return 1.0
        return min(1.0, self.suite_cpu_seconds / free_core_seconds)

    @property
    def outcome(self) -> SuiteBudgetOutcome:
        if self.suite_cpu_seconds > self.cpu_budget_seconds:
            return "over_cpu_budget"
        if self.wall_seconds <= self.wall_budget_seconds:
            return "within_budget"
        if self.adjusted_wall_seconds <= self.wall_budget_seconds:
            return "host_contention"
        if self.suite_utilisation >= CAPACITY_SATURATION:
            return "host_capacity"
        return "over_wall_budget"

    def announce(self) -> str | None:
        outcome = self.outcome
        if outcome == "host_contention":
            print(
                "WARNING: the tests took longer than the wall-clock budget only "
                "because other processes were using this host's CPUs; net of that "
                "contention the suite is within budget. Re-run on a quieter host "
                "to measure it alone.",
                file=sys.stderr,
                flush=True,
            )
            return None
        if outcome == "host_capacity":
            print(
                "WARNING: the tests took longer than the wall-clock budget because "
                "this host has too little CPU capacity for the suite: its own work "
                "kept the free cores saturated, so it could not have finished "
                "sooner. The CPU budget is the binding limit on this host.",
                file=sys.stderr,
                flush=True,
            )
            return None
        if outcome == "over_cpu_budget":
            return "suite CPU exceeded"
        if outcome == "over_wall_budget":
            return "wall clock exceeded even after crediting host contention"
        return None

    def describe(self) -> str:
        foreign_cores = self.foreign_cpu_seconds / self.wall_seconds if self.wall_seconds else 0.0
        return (
            f"wall {self.wall_seconds:.2f}s / {self.wall_budget_seconds:.1f}s "
            f"(contention-adjusted {self.adjusted_wall_seconds:.2f}s: other processes "
            f"kept {foreign_cores:.1f} of {self.cpu_count} cores busy; "
            f"suite kept {self.suite_utilisation:.0%} of the free cores busy), "
            f"suite CPU {self.suite_cpu_seconds:.1f} / {self.cpu_budget_seconds:.1f} core-s"
        )


__all__ = [
    "CAPACITY_SATURATION",
    "SuiteBudgetOutcome",
    "SuiteBudgetReport",
    "orphaned_cpu_tracker",
    "sample_suite_resources",
]
