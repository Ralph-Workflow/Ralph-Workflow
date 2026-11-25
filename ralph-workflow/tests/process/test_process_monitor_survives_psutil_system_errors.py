from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from ralph.process.monitor import DefaultProcessMonitor


class _ExitingDuringInspection:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def cmdline(self) -> list[str]:
        raise SystemError(
            "<built-in function proc_cmdline> returned a result with an exception set"
        )


@dataclass
class _Host:
    pid: int
    kids: list[object] = field(default_factory=list)
    broken_children: bool = False
    broken_cmdline: bool = False

    def cmdline(self) -> list[str]:
        if self.broken_cmdline:
            raise SystemError("proc_cmdline failed")
        return ["agy"]

    def children(self, *, recursive: bool = False) -> list[object]:
        del recursive
        if self.broken_children:
            raise SystemError("proc_children failed")
        return list(self.kids)


class _Healthy:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def cmdline(self) -> list[str]:
        return ["cargo", "build"]


def _monitor(host: _Host) -> DefaultProcessMonitor:
    return DefaultProcessMonitor(
        host.pid, process=lambda _pid: host, now=lambda: 1000.0, poll_interval_seconds=0.0
    )


def test_a_descendant_exiting_mid_inspection_is_skipped_not_fatal() -> None:
    host = _Host(pid=10, kids=[_ExitingDuringInspection(11), _Healthy(12)])

    processes = _monitor(host).classified_processes()

    assert {process.pid for process in processes} == {10, 12}


@pytest.mark.parametrize(
    "host",
    [_Host(pid=10, broken_children=True), _Host(pid=10, broken_cmdline=True)],
)
def test_a_host_that_psutil_cannot_inspect_never_crashes_the_monitor(host: _Host) -> None:
    monitor = _monitor(host)

    assert monitor.spawned_subagent_count() == 0
    assert [process.pid for process in monitor.classified_processes()] == [10]
