from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import pytest

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy, WatchdogVerdict
from ralph.agents.idle_watchdog.corroboration_snapshot import CorroborationSnapshot
from ralph.agents.invoke.busy_descendants import (
    BusyDescendantProbe,
    alive_by_with_busy_descendants,
    quiet_state_with_busy_descendants,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.process.child_liveness import AliveBy


@dataclass(frozen=True)
class _Times:
    user: float
    system: float


@dataclass
class _Proc:
    pid: int
    created: float
    cpu: float = 0.0
    kids: list[_Proc] = field(default_factory=list)

    def children(self, *, recursive: bool = False) -> Sequence[_Proc]:
        if not recursive:
            return list(self.kids)
        found: list[_Proc] = []
        for kid in self.kids:
            found.append(kid)
            found.extend(kid.children(recursive=True))
        return found

    def cpu_times(self) -> _Times:
        return _Times(user=self.cpu, system=0.0)

    def create_time(self) -> float:
        return self.created


class _Tree:
    def __init__(self, root: _Proc) -> None:
        self.root = root
        self.gone = False

    def process(self, pid: int) -> _Proc:
        if self.gone or pid != self.root.pid:
            raise ProcessLookupError(pid)
        return self.root


def _probe(tree: _Tree, clock: list[float]) -> BusyDescendantProbe:
    return BusyDescendantProbe(
        process=tree.process, clock=lambda: clock[0], sample_interval_seconds=0.0
    )


def test_probe_scans_the_process_table_at_most_once_per_sample_interval() -> None:
    find = _Proc(pid=11, created=990.0, cpu=5.0)
    tree = _Tree(_Proc(pid=10, created=0.0, kids=[find]))
    scans: list[int] = []

    def process(pid: int) -> _Proc:
        scans.append(pid)
        return tree.process(pid)

    now = [1000.0]
    probe = BusyDescendantProbe(process=process, clock=lambda: now[0], sample_interval_seconds=2.0)
    assert probe.busy(10) is True
    for _ in range(19):
        now[0] += 0.1
        assert probe.busy(10) is True
    assert len(scans) == 1
    now[0] += 0.2
    probe.busy(10)
    assert len(scans) == 2


def test_descendant_burning_cpu_is_busy() -> None:
    find = _Proc(pid=11, created=700.0, cpu=0.0)
    tree = _Tree(_Proc(pid=10, created=0.0, cpu=5.0, kids=[find]))
    now = [1000.0]
    probe = _probe(tree, now)
    probe.busy(10)
    for _ in range(10):
        now[0] += 1.0
        find.cpu += 0.4
        assert probe.busy(10) is True


def test_descendant_with_flat_cpu_is_not_busy_once_measured() -> None:
    hung = _Proc(pid=11, created=995.0, cpu=2.0)
    tree = _Tree(_Proc(pid=10, created=0.0, kids=[hung]))
    now = [1000.0]
    probe = _probe(tree, now)
    assert probe.busy(10) is True
    for _ in range(6):
        now[0] += 1.0
    assert probe.busy(10) is False


def test_first_sight_of_a_long_running_busy_child_is_busy() -> None:
    find = _Proc(pid=11, created=700.0, cpu=90.0)
    tree = _Tree(_Proc(pid=10, created=0.0, kids=[find]))
    assert _probe(tree, [1000.0]).busy(10) is True


def test_long_lived_idle_helper_is_not_busy() -> None:
    helper = _Proc(pid=11, created=0.0, cpu=0.5)
    tree = _Tree(_Proc(pid=10, created=0.0, kids=[helper]))
    assert _probe(tree, [3600.0]).busy(10) is False


def test_root_cpu_alone_never_counts_as_busy_descendants() -> None:
    tree = _Tree(_Proc(pid=10, created=999.0, cpu=50.0))
    assert _probe(tree, [1000.0]).busy(10) is False


def test_vanished_root_is_not_busy() -> None:
    tree = _Tree(_Proc(pid=10, created=0.0, kids=[_Proc(pid=11, created=990.0, cpu=9.0)]))
    tree.gone = True
    assert _probe(tree, [1000.0]).busy(10) is False


def test_grandchild_counts_as_a_descendant() -> None:
    grandchild = _Proc(pid=12, created=900.0, cpu=60.0)
    shell = _Proc(pid=11, created=900.0, cpu=0.0, kids=[grandchild])
    tree = _Tree(_Proc(pid=10, created=0.0, kids=[shell]))
    assert _probe(tree, [1000.0]).busy(10) is True


def test_new_root_pid_discards_the_previous_history() -> None:
    child = _Proc(pid=21, created=0.0, cpu=0.0)
    first = _Tree(_Proc(pid=10, created=0.0, kids=[_Proc(pid=11, created=0.0, cpu=0.0)]))
    second = _Tree(_Proc(pid=20, created=0.0, kids=[child]))
    now = [100.0]
    trees = {10: first, 20: second}
    probe = BusyDescendantProbe(
        process=lambda pid: trees[pid].process(pid),
        clock=lambda: now[0],
        sample_interval_seconds=0.0,
    )
    probe.busy(10)
    now[0] += 10.0
    child.cpu = 9.0
    assert probe.busy(20) is True


@pytest.mark.parametrize(
    ("reported", "busy", "expected"),
    [
        (AgentExecutionState.ACTIVE, True, AgentExecutionState.WAITING_ON_CHILD),
        (AgentExecutionState.ACTIVE, False, AgentExecutionState.ACTIVE),
        (AgentExecutionState.WAITING_ON_CHILD, False, AgentExecutionState.WAITING_ON_CHILD),
        (AgentExecutionState.RESUMABLE_CONTINUE, True, AgentExecutionState.RESUMABLE_CONTINUE),
    ],
)
def test_quiet_state_defers_only_an_active_report_with_busy_descendants(
    reported: AgentExecutionState, busy: bool, expected: AgentExecutionState
) -> None:
    assert quiet_state_with_busy_descendants(reported, lambda: busy) is expected


@pytest.mark.parametrize(
    ("alive_by", "busy", "expected"),
    [
        (None, True, AliveBy.OS_DESCENDANT_BUSY),
        (AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS, True, AliveBy.OS_DESCENDANT_BUSY),
        (
            AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            False,
            AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
        ),
        (AliveBy.FRESH_PROGRESS, True, AliveBy.FRESH_PROGRESS),
        (AliveBy.STALE_LABEL_ONLY, True, AliveBy.STALE_LABEL_ONLY),
    ],
)
def test_busy_descendants_upgrade_only_descendant_only_evidence(
    alive_by: AliveBy | None, busy: bool, expected: AliveBy | None
) -> None:
    assert alive_by_with_busy_descendants(alive_by, lambda: busy) == expected


_TICK_SECONDS = 1.0


def _run_silent_agent(
    busy_until: float, horizon: float
) -> tuple[float | None, Callable[[], float]]:
    clock = FakeClock(start=0.0)
    busy = [True]

    def corroborate() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            scoped_child_active=True,
            scoped_child_count=1,
            alive_by=alive_by_with_busy_descendants(
                AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS, lambda: busy[0]
            ),
        )

    watchdog = IdleWatchdog(
        TimeoutPolicy(idle_timeout_seconds=300.0),
        clock,
        corroborator=corroborate,
    )
    watchdog.record_invocation_start()
    watchdog.record_tool_use_activity()
    while clock.monotonic() < horizon:
        clock.advance(_TICK_SECONDS)
        busy[0] = clock.monotonic() < busy_until
        verdict = watchdog.evaluate(
            classify_quiet=lambda: quiet_state_with_busy_descendants(
                AgentExecutionState.ACTIVE, lambda: busy[0]
            )
        )
        if verdict == WatchdogVerdict.FIRE:
            return clock.monotonic(), clock.monotonic
    return None, clock.monotonic


def test_silent_agent_with_a_busy_child_survives_past_the_no_output_deadline() -> None:
    fired_at, _ = _run_silent_agent(busy_until=1200.0, horizon=1150.0)
    assert fired_at is None


def test_silent_agent_is_killed_soon_after_its_child_stops_working() -> None:
    fired_at, _ = _run_silent_agent(busy_until=900.0, horizon=2000.0)
    assert fired_at is not None
    assert 900.0 <= fired_at <= 960.0


def test_silent_agent_whose_child_never_stops_is_still_killed_at_the_child_wait_ceiling() -> None:
    fired_at, _ = _run_silent_agent(busy_until=10_000.0, horizon=4000.0)
    assert fired_at is not None
    assert fired_at <= 300.0 + 1800.0 + 60.0


def test_silent_agent_with_no_busy_child_is_killed_at_the_no_output_deadline() -> None:
    fired_at, _ = _run_silent_agent(busy_until=0.0, horizon=1000.0)
    assert fired_at is not None
    assert fired_at <= 300.0 + 60.0


@pytest.mark.parametrize("root_pid", [0, 1])
def test_probe_never_walks_the_whole_host_process_tree(root_pid: int) -> None:
    looked_up: list[int] = []

    def process(pid: int) -> _Proc:
        looked_up.append(pid)
        return _Proc(pid=pid, created=0.0, kids=[_Proc(pid=2, created=0.0, cpu=99.0)])

    probe = BusyDescendantProbe(process=process, clock=lambda: 100.0)

    assert probe.busy(root_pid) is False
    assert looked_up == []
