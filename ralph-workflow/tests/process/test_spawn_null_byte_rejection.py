"""Spawn arguments carrying a NUL byte must fail closed, not crash the run.

``subprocess.Popen`` raises ``ValueError("embedded null byte")`` from
CPython's ``_fork_exec`` when argv, env, or cwd contains ``"\\x00"``.
``ValueError`` is not an ``OSError``, so the spawn seams' failure
bookkeeping was skipped entirely: no FAILED ``ProcessRecord`` was
built, no lifecycle transition was emitted, and the bare
``ValueError("embedded null byte")`` -- which names neither the
argument nor the process -- unwound through the whole pipeline and
killed the run.

These tests pin the observable post-fix contract for all three spawn
seams (``spawn`` / ``spawn_pty`` / ``spawn_async``):

  * a NUL byte anywhere in command, cwd, or env is rejected BEFORE the
    process factory runs, so no child is ever launched;
  * the raised error names the offending argument, so an operator can
    act on it;
  * a FAILED lifecycle transition reaches every registered listener
    with that same legible message on the record.

A factory that raises ``ValueError`` for any other reason is recorded
the same way, so the seam can no longer drop a spawn failure on the
floor because of the exception's class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.process.manager import (
    ProcessEvent,
    ProcessManager,
    ProcessManagerPolicy,
    ProcessStatus,
    PtySpawnOptions,
    SpawnOptions,
)
from ralph.testing.fake_process import FakeAsyncProcess, FakePopen
from tests._process_manager_pty_helper__fakeprocess import _FakePtyProcess

if TYPE_CHECKING:
    from collections.abc import Sequence

_QUIET_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.0,
    kill_followup_timeout_s=0.0,
    log_events=False,
    enable_zombie_reaper=False,
)


class _RecordingSyncFactory:
    """Sync process factory that records whether it was ever invoked."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, command: Sequence[str], opts: SpawnOptions) -> FakePopen:
        del command, opts
        self.calls += 1
        return FakePopen(pid=4321)


class _RecordingPtyFactory:
    """PTY process factory that records whether it was ever invoked."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        cols: int,
        rows: int,
    ) -> _FakePtyProcess:
        del command, cwd, env, cols, rows
        self.calls += 1
        return _FakePtyProcess(pid=4322, master_fd=-1, slave_fd=-1)


class _RecordingAsyncFactory:
    """Async process factory that records whether it was ever invoked."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> FakeAsyncProcess:
        del command, cwd, env, stdin, stdout, stderr, start_new_session
        self.calls += 1
        return FakeAsyncProcess(pid=4323)


def _failed_events(events: Sequence[ProcessEvent]) -> list[ProcessEvent]:
    return [event for event in events if event.new_status is ProcessStatus.FAILED]


def test_spawn_rejects_null_byte_in_command_without_launching_a_child() -> None:
    """A NUL byte in argv must fail before the sync factory runs."""
    events: list[ProcessEvent] = []
    factory = _RecordingSyncFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, sync_process_factory=factory)
    manager.register_listener(events.append)

    with pytest.raises(ValueError, match="null byte") as excinfo:
        manager.spawn(["/bin/echo", "hello\x00world"])

    assert factory.calls == 0, "a command with a NUL byte must never reach the process factory"
    message = str(excinfo.value)
    assert "command[1]" in message, f"error must name the offending argument; got {message!r}"
    failed = _failed_events(events)
    assert len(failed) == 1, f"exactly one FAILED transition expected; got {events!r}"
    assert failed[0].record.failure_message == message
    assert failed[0].record.command == ("/bin/echo", "hello\x00world")
    assert manager.list_active() == []


def test_spawn_rejects_null_byte_in_env_value_and_names_the_variable() -> None:
    """A NUL byte in an environment value must name that variable."""
    events: list[ProcessEvent] = []
    factory = _RecordingSyncFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, sync_process_factory=factory)
    manager.register_listener(events.append)

    with pytest.raises(ValueError, match="null byte") as excinfo:
        manager.spawn(["/bin/echo"], SpawnOptions(env={"RALPH_TOKEN": "a\x00b"}))

    assert factory.calls == 0
    message = str(excinfo.value)
    assert "RALPH_TOKEN" in message, f"error must name the offending variable; got {message!r}"
    assert len(_failed_events(events)) == 1


def test_spawn_rejects_null_byte_in_cwd() -> None:
    """A NUL byte in the working directory must be rejected too."""
    events: list[ProcessEvent] = []
    factory = _RecordingSyncFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, sync_process_factory=factory)
    manager.register_listener(events.append)

    with pytest.raises(ValueError, match="null byte") as excinfo:
        manager.spawn(["/bin/echo"], SpawnOptions(cwd="/tmp/we\x00ird"))

    assert factory.calls == 0
    assert "cwd" in str(excinfo.value)
    assert len(_failed_events(events)) == 1


def test_spawn_records_failed_transition_when_factory_raises_value_error() -> None:
    """A sync factory ``ValueError`` must still produce a recorded spawn failure."""
    events: list[ProcessEvent] = []

    def failing_factory(command: Sequence[str], opts: SpawnOptions) -> FakePopen:
        del command, opts
        raise ValueError("embedded null byte")

    manager = ProcessManager(policy=_QUIET_POLICY, sync_process_factory=failing_factory)
    manager.register_listener(events.append)

    with pytest.raises(ValueError, match="embedded null byte"):
        manager.spawn(["/bin/echo", "ok"])

    failed = _failed_events(events)
    assert len(failed) == 1, f"factory ValueError must emit a FAILED transition; got {events!r}"
    assert failed[0].record.failure_message == "embedded null byte"
    assert failed[0].record.cause == "failed"


def test_spawn_pty_rejects_null_byte_in_command_without_launching_a_child() -> None:
    """The PTY seam rejects a NUL byte before the factory runs."""
    events: list[ProcessEvent] = []
    factory = _RecordingPtyFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, pty_process_factory=factory)
    manager.register_listener(events.append)

    with pytest.raises(ValueError, match="null byte") as excinfo:
        manager.spawn_pty(["claude", "--prompt\x00"])

    assert factory.calls == 0
    failed = _failed_events(events)
    assert len(failed) == 1, f"exactly one FAILED transition expected; got {events!r}"
    assert failed[0].record.failure_message == str(excinfo.value)
    assert manager.list_active() == []


def test_spawn_pty_rejects_null_byte_in_env_value() -> None:
    """The PTY seam validates its environment map as well."""
    events: list[ProcessEvent] = []
    factory = _RecordingPtyFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, pty_process_factory=factory)
    manager.register_listener(events.append)

    with pytest.raises(ValueError, match="null byte") as excinfo:
        manager.spawn_pty(["claude"], PtySpawnOptions(env={"RALPH_TOKEN": "a\x00b"}))

    assert factory.calls == 0
    assert "RALPH_TOKEN" in str(excinfo.value)
    assert len(_failed_events(events)) == 1


def test_spawn_pty_records_failed_transition_when_factory_raises_value_error() -> None:
    """A PTY factory ``ValueError`` must still produce a recorded spawn failure."""
    events: list[ProcessEvent] = []

    def failing_factory(
        command: Sequence[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        cols: int,
        rows: int,
    ) -> _FakePtyProcess:
        del command, cwd, env, cols, rows
        raise ValueError("embedded null byte")

    manager = ProcessManager(policy=_QUIET_POLICY, pty_process_factory=failing_factory)
    manager.register_listener(events.append)

    with pytest.raises(ValueError, match="embedded null byte"):
        manager.spawn_pty(["claude"])

    failed = _failed_events(events)
    assert len(failed) == 1, f"factory ValueError must emit a FAILED transition; got {events!r}"
    assert failed[0].record.failure_message == "embedded null byte"


@pytest.mark.asyncio
async def test_spawn_async_rejects_null_byte_in_command_without_launching_a_child() -> None:
    """The async seam rejects a NUL byte before the factory runs."""
    events: list[ProcessEvent] = []
    factory = _RecordingAsyncFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, async_process_factory=factory)
    manager.register_listener(events.append)

    with pytest.raises(ValueError, match="null byte") as excinfo:
        await manager.spawn_async(["claude", "--prompt\x00"])

    assert factory.calls == 0
    failed = _failed_events(events)
    assert len(failed) == 1, f"exactly one FAILED transition expected; got {events!r}"
    assert failed[0].record.failure_message == str(excinfo.value)
    assert manager.list_active() == []


@pytest.mark.asyncio
async def test_spawn_async_records_failed_transition_when_factory_raises_value_error() -> None:
    """An async factory ``ValueError`` must still produce a recorded spawn failure."""
    events: list[ProcessEvent] = []

    async def failing_factory(
        command: Sequence[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> FakeAsyncProcess:
        del command, cwd, env, stdin, stdout, stderr, start_new_session
        raise ValueError("embedded null byte")

    manager = ProcessManager(policy=_QUIET_POLICY, async_process_factory=failing_factory)
    manager.register_listener(events.append)

    with pytest.raises(ValueError, match="embedded null byte"):
        await manager.spawn_async(["claude"])

    failed = _failed_events(events)
    assert len(failed) == 1, f"factory ValueError must emit a FAILED transition; got {events!r}"
    assert failed[0].record.failure_message == "embedded null byte"
