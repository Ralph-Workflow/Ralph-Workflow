"""Tests for ManagedProcess.communicate_and_cleanup."""

from __future__ import annotations

import io
import itertools
import subprocess
import sys
import threading
import typing
from typing import TYPE_CHECKING, cast

import pytest

from ralph.process.manager import (
    ManagedProcess,
    ProcessManager,
    ProcessManagerPolicy,
    SpawnOptions,
)
from ralph.process.manager._managed_process_output_limit_exceeded_error import (
    ManagedProcessOutputLimitExceededError,
)
from ralph.process.manager._process_status import ProcessStatus
from ralph.testing._process_state import ProcessState
from ralph.testing._process_streams import ProcessStreams
from ralph.testing.fake_process import (
    FakePopen,
    FakePsutil,
    FakePsutilProcess,
    make_sync_process_factory,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.1,
    kill_followup_timeout_s=0.1,
    log_events=False,
    enable_zombie_reaper=False,
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


def test_cleans_snapshot_survivors_and_late_spawns() -> None:
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


def test_missing_root_still_returns_output() -> None:
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


def test_process_observer_regression_permission_error_does_not_escape_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: macOS sandbox denial must not escape the observer thread."""
    attempted = threading.Event()

    class PermissionDeniedRoot(TreeProcess):
        def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
            del recursive
            attempted.set()
            raise PermissionError

    fake_psutil = FakePsutil()
    fake_psutil._processes = {1: PermissionDeniedRoot(pid=1)}
    handle = _make_handle(fake_psutil=fake_psutil)
    thread_errors: list[BaseException | None] = []
    monkeypatch.setattr(
        threading,
        "excepthook",
        lambda args: thread_errors.append(args.exc_value),
    )

    def communicate(
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, bytes]:
        del input, timeout
        assert attempted.wait(timeout=0.5)
        return b"ok", b""

    handle._proc.communicate = communicate
    stdout, stderr = handle.communicate_and_cleanup()

    assert stdout == b"ok"
    assert stderr == b""
    assert thread_errors == []


def test_process_observer_regression_keeps_child_seen_before_permission_error() -> None:
    """Regression: a denied later scan must not discard an observed child."""
    child = TreeProcess(pid=1001, stubborn=True)
    observed = threading.Event()

    class PermissionDeniedAfterObservationRoot(TreeProcess):
        def __init__(self) -> None:
            super().__init__(pid=1)
            self._scan_count = 0

        def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
            del recursive
            self._scan_count += 1
            if self._scan_count == 1:
                observed.set()
                return [child]
            raise PermissionError

    root = PermissionDeniedAfterObservationRoot()
    fake_psutil = FakePsutil()
    fake_psutil._processes = {1: root, child.pid: child}
    handle = _make_handle(fake_psutil=fake_psutil)

    def communicate(
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, bytes]:
        del input, timeout
        assert observed.wait(timeout=0.5)
        return b"ok", b""

    handle._proc.communicate = communicate
    stdout, stderr = handle.communicate_and_cleanup()

    assert stdout == b"ok"
    assert stderr == b""
    assert child._killed is True


def test_kills_root_late_spawn_descendants() -> None:
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


def test_kills_all_snapshot_descendants() -> None:
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


def test_kills_descendants_of_snapshot_survivors() -> None:
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


def test_kills_second_level_late_spawn_descendants() -> None:
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


def test_marks_process_as_exited() -> None:
    fake_psutil = FakePsutil()
    handle = _make_handle(fake_psutil=fake_psutil)
    handle._proc.communicate = lambda input=None, timeout=None: (b"ok", b"")

    stdout, stderr = handle.communicate_and_cleanup()

    assert stdout == b"ok"
    assert stderr == b""
    assert handle.record.status == ProcessStatus.EXITED


def test_handles_no_psutil_gracefully() -> None:
    handle = _make_handle(fake_psutil=None)
    handle._proc.communicate = lambda input=None, timeout=None: (b"ok", b"")

    stdout, stderr = handle.communicate_and_cleanup()

    assert stdout == b"ok"
    assert stderr == b""
    assert handle.record.status == ProcessStatus.EXITED


def test_output_limit_cleanup_kills_descendants_and_returns_tail() -> None:
    child = TreeProcess(pid=1001, stubborn=True)
    root = TreeProcess(pid=1, direct_children=[child], recursive_children=[child])
    fake_psutil = FakePsutil()
    fake_psutil._processes = {1: root, 1001: child}

    class StreamingFakePopen(FakePopen):
        def __init__(self) -> None:
            super().__init__(
                pid=1,
                state=ProcessState(returncode=None),
                streams=ProcessStreams(
                    stdout=io.BytesIO(b"abcdef123456"),
                    stderr=io.BytesIO(b"stderr-tail"),
                ),
            )

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            if self._returncode is None:
                self._returncode = 137 if (self._terminated or self._killed) else 0
            return self._returncode

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=lambda command, opts: StreamingFakePopen(),
        psutil=cast("typing.Any", fake_psutil),
    )
    handle = pm.spawn([sys.executable, "-c", "pass"], SpawnOptions(label="test:managed-process"))

    with pytest.raises(ManagedProcessOutputLimitExceededError) as excinfo:
        handle.communicate_and_cleanup(output_limit_bytes=8)

    assert excinfo.value.stdout == b"ef123456"
    assert excinfo.value.stderr == b"err-tail"
    assert child._killed is True


def test_already_dead_descendants_are_ignored() -> None:
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


def test_timeout_kills_snapshot_descendants() -> None:
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


# ---------------------------------------------------------------------------
# Streaming: on_output_chunk callback
# ---------------------------------------------------------------------------


class _StreamingFakePopen(FakePopen):
    """Fake Popen that returns fixed stdout/stderr bytes for chunk streaming tests."""

    def __init__(self, stdout_data: bytes = b"", stderr_data: bytes = b"") -> None:
        super().__init__(
            pid=1,
            state=ProcessState(returncode=None),
            streams=ProcessStreams(
                stdout=io.BytesIO(stdout_data),
                stderr=io.BytesIO(stderr_data),
            ),
        )

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self._returncode is None:
            self._returncode = 0
        return self._returncode


def _make_streaming_handle(
    stdout_data: bytes = b"",
    stderr_data: bytes = b"",
) -> ManagedProcess:
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=lambda command, opts: _StreamingFakePopen(stdout_data, stderr_data),
        psutil=None,
    )
    return pm.spawn([sys.executable, "-c", "pass"], SpawnOptions(label="test:stream"))


def test_on_output_chunk_receives_stdout_bytes_before_completion() -> None:
    """communicate_and_cleanup calls on_output_chunk with stdout bytes before returning."""
    handle = _make_streaming_handle(stdout_data=b"hello-stdout", stderr_data=b"")

    received: list[bytes] = []
    stdout, _stderr = handle.communicate_and_cleanup(
        output_limit_bytes=4096,
        on_output_chunk=received.append,
    )

    assert received, "on_output_chunk must be called at least once"
    combined = b"".join(received)
    assert b"hello-stdout" in combined
    assert stdout == b"hello-stdout"


def test_on_output_chunk_receives_stderr_bytes_before_completion() -> None:
    """communicate_and_cleanup calls on_output_chunk with stderr bytes before returning."""
    handle = _make_streaming_handle(stdout_data=b"", stderr_data=b"hello-stderr")

    received: list[bytes] = []
    _stdout, stderr = handle.communicate_and_cleanup(
        output_limit_bytes=4096,
        on_output_chunk=received.append,
    )

    assert received, "on_output_chunk must be called for stderr chunks"
    combined = b"".join(received)
    assert b"hello-stderr" in combined
    assert stderr == b"hello-stderr"


def test_on_output_chunk_does_not_prevent_output_limit_error() -> None:
    """on_output_chunk callback does not suppress ManagedProcessOutputLimitExceededError."""
    handle = _make_streaming_handle(
        stdout_data=b"123456789012345",
        stderr_data=b"err-data",
    )

    received: list[bytes] = []
    with pytest.raises(ManagedProcessOutputLimitExceededError):
        handle.communicate_and_cleanup(
            output_limit_bytes=8,
            on_output_chunk=received.append,
        )

    assert received, "on_output_chunk must still be called even when limit is exceeded"


def test_timeout_still_terminates_root(monkeypatch: pytest.MonkeyPatch) -> None:
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


# ---------------------------------------------------------------------------
# Cost contract: one synchronous process-tree scan per call
# ---------------------------------------------------------------------------


class _CountingTreeProcess(TreeProcess):
    """A :class:`TreeProcess` that records every recursive-tree scan."""

    def __init__(self, pid: int, scans: list[str], **kwargs: object) -> None:
        super().__init__(pid, **cast("typing.Any", kwargs))
        self._scans = scans

    def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
        if recursive:
            self._scans.append("recursive-tree-scan")
        return super().children(recursive=recursive)


def test_successful_call_takes_exactly_one_recursive_tree_scan() -> None:
    """A managed subprocess must cost ONE recursive process-tree scan, not two.

    ``psutil.Process.children(recursive=True)`` walks every pid on the
    machine to build a ppid map. Measured on a developer machine with
    ~900 live processes it costs ~13 ms -- about the same as running a
    whole short ``git`` command -- and it grows with the machine's total
    process count, so it degrades precisely when several Ralph agents
    run side by side. ``communicate_and_cleanup`` used to take a second
    scan *before* handing control to the child; that scan ran
    microseconds after ``spawn`` and so could only ever observe the tree
    the trailing scan observes again. This test pins the cost contract
    so the redundant scan cannot creep back in.
    """
    scans: list[str] = []
    root = _CountingTreeProcess(pid=1, scans=scans)
    fake_psutil = FakePsutil()
    fake_psutil._processes = {1: root}
    handle = _make_handle(fake_psutil=fake_psutil)
    handle._proc.communicate = lambda input=None, timeout=None: (b"ok", b"")

    stdout, stderr = handle.communicate_and_cleanup()

    assert stdout == b"ok"
    assert stderr == b""
    assert len(scans) == 1, (
        f"expected exactly one recursive process-tree scan, got {len(scans)}; "
        "each redundant scan costs roughly as much as the subprocess itself"
    )


def test_single_scan_still_reaps_a_descendant_spawned_while_running() -> None:
    """The surviving trailing scan must still find and kill late descendants.

    Guards the optimisation pinned by
    :func:`test_successful_call_takes_exactly_one_recursive_tree_scan`
    against becoming a correctness regression: dropping the pre-run scan
    must not drop any reaping coverage.
    """
    scans: list[str] = []
    late_spawn = TreeProcess(pid=2001, stubborn=True)
    root = _CountingTreeProcess(pid=1, scans=scans)
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

    stdout, _stderr = handle.communicate_and_cleanup()

    assert stdout == b"ok"
    assert late_spawn._killed is True
    assert len(scans) == 1
