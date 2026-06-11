"""Black-box unit tests for SubprocessAgentExecutor.

Tests verify that SubprocessAgentExecutor:
- spawns processes with correct label via ProcessManager
- tracks processes to terminal state after normal completion
- marks processes KILLED when cancelled
- cleans up the process when a non-CancelledError exception escapes drain_output

No real subprocesses are spawned; all tests use FakeControllableAsyncProcess
and FakePsutil injected via a custom ProcessManager.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import sys

import pytest

from ralph.agents.subprocess_executor import (
    SubprocessAgentExecutor,
    agent_process_label,
)
from ralph.pipeline.work_units import WorkUnit
from ralph.process import ProcessManager, ProcessManagerPolicy, ProcessStatus
from ralph.testing.fake_process import FakeControllableAsyncProcess, FakePsutil, FakePsutilProcess

# --------------------------------------------------------------------------/
# CAT-AGENT-UNIT: normal completion + label contract
# --------------------------------------------------------------------------/


@pytest.mark.asyncio
async def test_subprocess_executor_tracks_process_with_correct_label() -> None:
    """SubprocessAgentExecutor.run() spawns with agent_process_label and tracks."""

    # Instant completion event
    event = asyncio.Event()
    event.set()

    pid_counter = itertools.count(1)

    async def async_factory(
        command: tuple[str, ...],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> FakeControllableAsyncProcess:
        return FakeControllableAsyncProcess(
            pid=next(pid_counter),
            completion_event=event,
        )

    pm = ProcessManager(
        async_process_factory=async_factory,
        psutil=FakePsutil(),
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.3,
            kill_followup_timeout_s=0.5,
            log_events=False,
            enable_zombie_reaper=False,
        ),
    )

    executor = SubprocessAgentExecutor(
        command=[sys.executable, "-c", "pass"],
        _pm=pm,
    )

    unit = WorkUnit(unit_id="unit-1", description="test")

    await executor.run(
        unit,
        on_output=lambda _: None,
        on_status=lambda _: None,
    )

    # Verify exactly one terminal record
    records = pm.list_records(include_active=False, include_terminal=True)
    assert len(records) == 1, f"Expected 1 terminal record, got {len(records)}"

    record = records[0]
    expected_label = agent_process_label("unit-1", None)
    assert record.label == expected_label, (
        f"Expected label '{expected_label}', got '{record.label}'"
    )
    assert record.status in (ProcessStatus.EXITED, ProcessStatus.KILLED)


# --------------------------------------------------------------------------/
# CAT-AGENT-CANCEL: cancellation/shutdown path
# --------------------------------------------------------------------------/


@pytest.mark.asyncio
async def test_subprocess_executor_cancellation_marks_process_killed() -> None:
    """Cancelling an in-flight executor.run() marks process KILLED."""

    # Process stays alive indefinitely (event not set)
    event = asyncio.Event()

    pid_counter = itertools.count(100)

    async def async_factory(
        command: tuple[str, ...],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> FakeControllableAsyncProcess:
        return FakeControllableAsyncProcess(
            pid=next(pid_counter),
            completion_event=event,
        )

    # Use FakePsutil so cancellation routes through _escalate_termination_async's
    # psutil branch (_do_terminate in run_in_executor). FakePsutil.process_from_pid
    # raises NoSuchProcess for unregistered pids, so _do_terminate returns False
    # (still_alive=False) and _mark_killed is called cleanly without ProcessTerminationError.
    pm = ProcessManager(
        async_process_factory=async_factory,
        psutil=FakePsutil(),
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0,
            kill_followup_timeout_s=0.0,
            log_events=False,
            enable_zombie_reaper=False,
        ),
    )

    executor = SubprocessAgentExecutor(
        command=[sys.executable, "-c", "pass"],
        _pm=pm,
    )

    unit = WorkUnit(unit_id="unit-cancel-1", description="test")

    task = asyncio.create_task(
        executor.run(
            unit,
            on_output=lambda _: None,
            on_status=lambda _: None,
        )
    )

    # Yield twice to allow spawn_async() to complete and task to block at asyncio.gather
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(pm.list_active()) == 1, (
        f"Expected 1 active process before cancel, got {len(pm.list_active())}"
    )

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    # Verify exactly one terminal record with KILLED status
    records = pm.list_records(include_active=False, include_terminal=True)
    assert len(records) == 1, f"Expected 1 terminal record, got {len(records)}"
    assert records[0].status == ProcessStatus.KILLED

    # Verify list_active is empty
    assert len(pm.list_active()) == 0


# --------------------------------------------------------------------------
# CAT-AGENT-EXC: cleanup-on-exception
# --------------------------------------------------------------------------/


class _ExCAsyncProcess:
    """Async-process stub with stdout data and a completeable wait().

    Mirrors the surface of FakeControllableAsyncProcess used by the
    SubprocessAgentExecutor (pid, stdout StreamReader, wait/terminate/kill
    methods, returncode property). ``wait()`` blocks on an asyncio.Event
    that is only set when ``terminate()`` or ``kill()`` is invoked, OR
    when ``set_completion()`` is called explicitly.

    The production code's terminate path uses psutil to signal the child
    rather than calling this stub's ``terminate()`` directly. So the
    test also relies on a sibling psutil fake invoking
    ``set_completion()`` when the psutil side is killed, which keeps
    the executor's finally-block ``wait_for(handle.wait(), timeout=0.5)``
    bounded to a fraction of a millisecond instead of hitting the full
    500ms cap. This keeps the test well under the 1.0s per-test budget.
    """

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._returncode: int | None = None
        self._completion_event = asyncio.Event()
        self._stream_reader: asyncio.StreamReader | None = None
        self.stdin = None
        self.stderr = None

    def _ensure_stdout(self) -> asyncio.StreamReader:
        if self._stream_reader is None:
            self._stream_reader = asyncio.StreamReader()
            self._stream_reader.feed_data(b"boom\n")
            self._stream_reader.feed_eof()
        return self._stream_reader

    @property
    def stdout(self) -> asyncio.StreamReader:
        return self._ensure_stdout()

    @property
    def returncode(self) -> int | None:
        return self._returncode

    async def wait(self) -> int:
        await asyncio.wait_for(self._completion_event.wait(), timeout=0.5)
        if self._returncode is None:
            self._returncode = 0
        return self._returncode

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        await asyncio.wait_for(self._completion_event.wait(), timeout=0.5)
        return b"", b""

    def terminate(self) -> None:
        self._returncode = 0
        self._completion_event.set()

    def kill(self) -> None:
        self._returncode = 0
        self._completion_event.set()

    def set_completion(self) -> None:
        """Mark the process as completed without flipping returncode.

        Used by sibling psutil fakes to signal that the production
        code's psutil.terminate()/kill() has reached the OS-level
        kill, so the executor's finally-block wait can return.
        """
        if self._returncode is None:
            self._returncode = 0
        self._completion_event.set()


@pytest.mark.asyncio
async def test_subprocess_executor_cleans_up_on_non_cancellation_exception() -> None:
    """Non-CancelledError exception from on_output triggers process cleanup.

    The finally block in SubprocessAgentExecutor.run() must terminate the
    spawned process and mark the record KILLED when a non-CancelledError
    exception escapes the asyncio.gather(). This proves the executor is no
    longer a "leak the process when output parsing crashes" hole.
    """

    # Track spawned async procs so the psutil fake can signal them when
    # the production terminate path runs psutil.kill. This keeps the
    # executor's wait_for(handle.wait(), timeout=0.5) bounded to a
    # fraction of a millisecond instead of hitting the full 500ms cap.
    spawned_procs: dict[int, _ExCAsyncProcess] = {}
    pid_counter = itertools.count(200)

    async def async_factory(
        command: tuple[str, ...],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> _ExCAsyncProcess:
        proc = _ExCAsyncProcess(pid=next(pid_counter))
        spawned_procs[proc.pid] = proc
        return proc

    class _LinkedPsutilProcess(FakePsutilProcess):
        def kill(self) -> None:
            super().kill()
            linked = spawned_procs.get(self.pid)
            if linked is not None:
                linked.set_completion()

        def terminate(self) -> None:
            super().terminate()
            linked = spawned_procs.get(self.pid)
            if linked is not None:
                linked.set_completion()

    class _LinkedFakePsutil(FakePsutil):
        def process_from_pid(self, pid: int) -> _LinkedPsutilProcess:
            existing = self._processes.get(pid)
            if existing is None:
                existing = _LinkedPsutilProcess(pid=pid)
                self._processes[pid] = existing
            return existing

    pm = ProcessManager(
        async_process_factory=async_factory,
        psutil=_LinkedFakePsutil(),
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0,
            kill_followup_timeout_s=0.0,
            log_events=False,
            enable_zombie_reaper=False,
        ),
    )

    executor = SubprocessAgentExecutor(
        command=[sys.executable, "-c", "pass"],
        _pm=pm,
    )

    unit = WorkUnit(unit_id="unit-exc-1", description="test")

    def on_output(_: str) -> None:
        raise RuntimeError("on_output boom")

    with pytest.raises(RuntimeError, match="on_output boom"):
        await executor.run(
            unit,
            on_output=on_output,
            on_status=lambda _: None,
        )

    # Verify the process record became terminal with KILLED status.
    records = pm.list_records(include_active=False, include_terminal=True)
    assert len(records) == 1, f"Expected 1 terminal record, got {len(records)}"
    assert records[0].status == ProcessStatus.KILLED

    # Verify no active records remain — the cleanup ran.
    assert pm.list_active() == []

