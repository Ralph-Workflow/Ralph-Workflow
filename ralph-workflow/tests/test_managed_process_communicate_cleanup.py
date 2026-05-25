"""Tests for ManagedProcess.communicate_and_cleanup."""

from __future__ import annotations

import itertools
import subprocess
import sys
import typing
from typing import TYPE_CHECKING, cast

import pytest

from ralph.process.manager import ManagedProcess, ProcessManager, ProcessManagerPolicy, SpawnOptions
from ralph.process.manager._process_status import ProcessStatus
from ralph.testing.fake_process import FakePsutil, FakePsutilProcess, make_sync_process_factory

if TYPE_CHECKING:
    from collections.abc import Sequence

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.1,
    kill_followup_timeout_s=0.1,
    log_events=False,
)


class TreeProcess(FakePsutilProcess):
    def __init__(
        self,
        pid: int,
        *,
        direct_children: Sequence[FakePsutilProcess] | None = None,
        recursive_children: Sequence[FakePsutilProcess] | None = None,
        _running: bool = True,
        _status: str = "sleeping",
        _create_time: float = 0.0,
        _terminated: bool = False,
        _killed: bool = False,
        stubborn: bool = False,
    ) -> None:
        super().__init__(
            pid=pid,
            _running=_running,
            _status=_status,
            _create_time=_create_time,
            _terminated=_terminated,
            _killed=_killed,
            stubborn=stubborn,
        )
        self._direct_children: Sequence[FakePsutilProcess] = list(direct_children or [])
        self._recursive_children: Sequence[FakePsutilProcess] = list(recursive_children or [])

    def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
        return list(self._recursive_children if recursive else self._direct_children)


def _make_handle(
    *,
    fake_psutil: FakePsutil | None,
    returncode: int | None = 0,
) -> ManagedProcess:
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(1), returncode=returncode),
        psutil=cast("typing.Any", fake_psutil),
    )
    return pm.spawn([sys.executable, "-c", "pass"], SpawnOptions(label="test:managed-process"))


class TestManagedProcessCommunicateAndCleanup:
    def test_cleans_snapshot_survivors_and_late_spawns(self) -> None:
        live_child = TreeProcess(pid=1001, stubborn=True)
        live_grandchild = TreeProcess(pid=1002, stubborn=True)
        late_spawn = TreeProcess(pid=2001, stubborn=True)
        second_level = TreeProcess(pid=3001, stubborn=True)

        root = TreeProcess(
            pid=1,
            direct_children=[live_child],
            recursive_children=[live_child, live_grandchild],
        )
        fake_psutil = FakePsutil()
        fake_psutil._processes = {
            1: root,
            1001: live_child,
            1002: live_grandchild,
            2001: late_spawn,
            3001: second_level,
        }
        handle = _make_handle(fake_psutil=fake_psutil)

        def communicate(
            input: bytes | None = None,
            timeout: float | None = None,
        ) -> tuple[bytes, bytes]:
            del input, timeout
            live_child._direct_children = [late_spawn]
            late_spawn._direct_children = [second_level]
            return b"out", b"err"

        handle._proc.communicate = communicate

        stdout, stderr = handle.communicate_and_cleanup()

        assert stdout == b"out"
        assert stderr == b"err"
        assert handle.record.status == ProcessStatus.EXITED
        assert live_child._killed is True
        assert live_child._terminated is False
        assert live_grandchild._killed is True
        assert late_spawn._killed is True
        assert second_level._killed is True

    def test_missing_root_still_returns_output(self) -> None:
        class MissingRootPsutil(FakePsutil):
            def process_from_pid(self, pid: int) -> FakePsutilProcess:
                raise self.NoSuchProcess

        fake_psutil = MissingRootPsutil()
        handle = _make_handle(fake_psutil=fake_psutil)
        handle._proc.communicate = lambda input=None, timeout=None: (b"ok", b"err")

        stdout, stderr = handle.communicate_and_cleanup()

        assert stdout == b"ok"
        assert stderr == b"err"
        assert handle.record.status == ProcessStatus.EXITED

    def test_kills_root_late_spawn_descendants(self) -> None:
        late_spawn = TreeProcess(pid=2001, stubborn=True)
        root = TreeProcess(pid=1)
        fake_psutil = FakePsutil()
        fake_psutil._processes = {1: root, 2001: late_spawn}
        handle = _make_handle(fake_psutil=fake_psutil)

        def communicate(
            input: bytes | None = None,
            timeout: float | None = None,
        ) -> tuple[bytes, bytes]:
            del input, timeout
            root._direct_children = [late_spawn]
            root._recursive_children = [late_spawn]
            return b"ok", b""

        handle._proc.communicate = communicate

        stdout, stderr = handle.communicate_and_cleanup()

        assert stdout == b"ok"
        assert stderr == b""
        assert late_spawn._killed is True
        assert handle.record.status == ProcessStatus.EXITED

    def test_kills_all_snapshot_descendants(self) -> None:
        child_one = TreeProcess(pid=1001, stubborn=True)
        child_two = TreeProcess(pid=1002, stubborn=True)
        child_three = TreeProcess(pid=1003, stubborn=True)
        root = TreeProcess(
            pid=1,
            direct_children=[child_one, child_two, child_three],
            recursive_children=[child_one, child_two, child_three],
        )
        fake_psutil = FakePsutil()
        fake_psutil._processes = {
            1: root,
            1001: child_one,
            1002: child_two,
            1003: child_three,
        }
        handle = _make_handle(fake_psutil=fake_psutil)
        handle._proc.communicate = lambda input=None, timeout=None: (b"ok", b"")

        stdout, stderr = handle.communicate_and_cleanup()

        assert stdout == b"ok"
        assert stderr == b""
        assert child_one._killed is True
        assert child_two._killed is True
        assert child_three._killed is True

    def test_kills_descendants_of_snapshot_survivors(self) -> None:
        child = TreeProcess(pid=1001, stubborn=True)
        grandchild = TreeProcess(pid=2001, stubborn=True)
        root = TreeProcess(pid=1, direct_children=[child], recursive_children=[child])
        fake_psutil = FakePsutil()
        fake_psutil._processes = {1: root, 1001: child, 2001: grandchild}
        handle = _make_handle(fake_psutil=fake_psutil)

        def communicate(
            input: bytes | None = None,
            timeout: float | None = None,
        ) -> tuple[bytes, bytes]:
            del input, timeout
            child._direct_children = [grandchild]
            return b"ok", b""

        handle._proc.communicate = communicate

        stdout, stderr = handle.communicate_and_cleanup()

        assert stdout == b"ok"
        assert stderr == b""
        assert child._killed is True
        assert grandchild._killed is True

    def test_kills_second_level_late_spawn_descendants(self) -> None:
        child = TreeProcess(pid=1001, stubborn=True)
        grandchild = TreeProcess(pid=2001, stubborn=True)
        great_grandchild = TreeProcess(pid=3001, stubborn=True)
        root = TreeProcess(pid=1, direct_children=[child], recursive_children=[child])
        fake_psutil = FakePsutil()
        fake_psutil._processes = {
            1: root,
            1001: child,
            2001: grandchild,
            3001: great_grandchild,
        }
        handle = _make_handle(fake_psutil=fake_psutil)

        def communicate(
            input: bytes | None = None,
            timeout: float | None = None,
        ) -> tuple[bytes, bytes]:
            del input, timeout
            child._direct_children = [grandchild]
            grandchild._direct_children = [great_grandchild]
            return b"ok", b""

        handle._proc.communicate = communicate

        stdout, stderr = handle.communicate_and_cleanup()

        assert stdout == b"ok"
        assert stderr == b""
        assert child._killed is True
        assert grandchild._killed is True
        assert great_grandchild._killed is True

    def test_marks_process_as_exited(self) -> None:
        fake_psutil = FakePsutil()
        handle = _make_handle(fake_psutil=fake_psutil)
        handle._proc.communicate = lambda input=None, timeout=None: (b"ok", b"")

        stdout, stderr = handle.communicate_and_cleanup()

        assert stdout == b"ok"
        assert stderr == b""
        assert handle.record.status == ProcessStatus.EXITED

    def test_handles_no_psutil_gracefully(self) -> None:
        handle = _make_handle(fake_psutil=None)
        handle._proc.communicate = lambda input=None, timeout=None: (b"ok", b"")

        stdout, stderr = handle.communicate_and_cleanup()

        assert stdout == b"ok"
        assert stderr == b""
        assert handle.record.status == ProcessStatus.EXITED

    def test_already_dead_descendants_are_ignored(self) -> None:
        live_child = TreeProcess(pid=1001, stubborn=True)
        dead_child = TreeProcess(pid=1002, _running=True, _status="zombie")
        root = TreeProcess(
            pid=1,
            direct_children=[live_child, dead_child],
            recursive_children=[live_child, dead_child],
        )
        fake_psutil = FakePsutil()
        fake_psutil._processes = {
            1: root,
            1001: live_child,
            1002: dead_child,
        }
        handle = _make_handle(fake_psutil=fake_psutil)
        handle._proc.communicate = lambda input=None, timeout=None: (b"ok", b"")

        stdout, stderr = handle.communicate_and_cleanup()

        assert stdout == b"ok"
        assert stderr == b""
        assert live_child._killed is True
        assert dead_child._killed is False
        assert dead_child._terminated is False

    def test_timeout_kills_snapshot_descendants(self) -> None:
        live_child = TreeProcess(pid=1001, stubborn=True)
        root = TreeProcess(
            pid=1,
            direct_children=[live_child],
            recursive_children=[live_child],
        )
        fake_psutil = FakePsutil()
        fake_psutil._processes = {1: root, 1001: live_child}
        handle = _make_handle(fake_psutil=fake_psutil)
        handle._proc.communicate = lambda input=None, timeout=None: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(cmd=[sys.executable], timeout=0.1)
        )

        with pytest.raises(subprocess.TimeoutExpired):
            handle.communicate_and_cleanup(cleanup_grace_period_s=0.0)

        assert live_child._killed, (
            "Live snapshot descendants must be killed by communicate_and_cleanup "
            "timeout handler, independent of any exec-level orphan sweeper"
        )

    def test_timeout_still_terminates_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_psutil = FakePsutil()
        handle = _make_handle(fake_psutil=fake_psutil)
        handle._proc.communicate = lambda input=None, timeout=None: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(cmd=[sys.executable], timeout=0.1)
        )
        seen: list[float | None] = []
        monkeypatch.setattr(
            handle,
            "terminate",
            lambda grace_period_s=None: seen.append(grace_period_s),
        )

        with pytest.raises(subprocess.TimeoutExpired):
            handle.communicate_and_cleanup(cleanup_grace_period_s=0.25)

        assert seen == [0.25]
