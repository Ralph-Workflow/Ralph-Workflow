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


@pytest.mark.asyncio
async def test_subprocess_executor_tracks_process_with_correct_label() -> None:

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

    records = pm.list_records(include_active=False, include_terminal=True)
    assert len(records) == 1, f"Expected 1 terminal record, got {len(records)}"

    record = records[0]
    expected_label = agent_process_label("unit-1", None)
    assert record.label == expected_label, (
        f"Expected label '{expected_label}', got '{record.label}'"
    )
    assert record.status in (ProcessStatus.EXITED, ProcessStatus.KILLED)


@pytest.mark.asyncio
async def test_subprocess_executor_cancellation_marks_process_killed() -> None:

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

    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(pm.list_active()) == 1, (
        f"Expected 1 active process before cancel, got {len(pm.list_active())}"
    )

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    records = pm.list_records(include_active=False, include_terminal=True)
    assert len(records) == 1, f"Expected 1 terminal record, got {len(records)}"
    assert records[0].status == ProcessStatus.KILLED

    assert len(pm.list_active()) == 0


class _ExCAsyncProcess:
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

    def terminate(self, **_kwargs: object) -> None:
        self._returncode = 0
        self._completion_event.set()

    def kill(self, **_kwargs: object) -> None:
        self._returncode = 0
        self._completion_event.set()

    def set_completion(self) -> None:
        if self._returncode is None:
            self._returncode = 0
        self._completion_event.set()


@pytest.mark.asyncio
async def test_subprocess_executor_cleans_up_on_non_cancellation_exception() -> None:

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
        def kill(self, **_kwargs: object) -> None:
            super().kill()
            linked = spawned_procs.get(self.pid)
            if linked is not None:
                linked.set_completion()

        def terminate(self, **_kwargs: object) -> None:
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

    records = pm.list_records(include_active=False, include_terminal=True)
    assert len(records) == 1, f"Expected 1 terminal record, got {len(records)}"
    assert records[0].status == ProcessStatus.KILLED

    assert pm.list_active() == []
