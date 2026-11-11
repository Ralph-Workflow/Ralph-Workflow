from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.process import validate_spawn_arguments
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
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, command: Sequence[str], opts: SpawnOptions) -> FakePopen:
        del command, opts
        self.calls += 1
        return FakePopen(pid=4321)


class _RecordingPtyFactory:
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


def test_spawn_rejects_null_byte_in_the_program_name_without_launching_a_child() -> None:
    events: list[ProcessEvent] = []
    factory = _RecordingSyncFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, sync_process_factory=factory)
    manager.register_listener(events.append)

    with pytest.raises(ValueError, match=r"command\[0\]") as excinfo:
        manager.spawn(["/bin/ec\x00ho", "hello"])

    assert factory.calls == 0, (
        "a program name with a NUL byte must never reach the injected process "
        "factory, which is the seam every real child is created through"
    )
    failed = _failed_events(events)
    assert len(failed) == 1, f"exactly one FAILED transition expected; got {events!r}"
    assert failed[0].record.failure_message == str(excinfo.value)
    assert failed[0].record.command == ("/bin/ec\x00ho", "hello")
    assert manager.list_active() == []


def test_spawn_strips_a_null_byte_from_an_argument_and_launches_the_child() -> None:
    events: list[ProcessEvent] = []
    factory = _RecordingSyncFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, sync_process_factory=factory)
    manager.register_listener(events.append)

    handle = manager.spawn(["/bin/echo", "hello\x00world"])

    assert factory.calls == 1
    assert handle.record.command == ("/bin/echo", "helloworld")
    assert _failed_events(events) == []


def test_spawn_rejects_null_byte_in_env_value_and_names_the_variable() -> None:
    events: list[ProcessEvent] = []
    factory = _RecordingSyncFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, sync_process_factory=factory)
    manager.register_listener(events.append)

    with pytest.raises(ValueError, match="null byte") as excinfo:
        manager.spawn(["/bin/echo"], SpawnOptions(env={"RALPH_TOKEN": "a\x00b"}))

    assert factory.calls == 0
    message = str(excinfo.value)
    assert "RALPH_TOKEN" in message, f"error must name the offending variable; got {message!r}"
    assert "a\x00b" not in message, (
        f"the offending value must never be interpolated into the message; got {message!r}"
    )
    assert len(_failed_events(events)) == 1


def test_spawn_rejects_null_byte_in_cwd() -> None:
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


def test_spawn_pty_rejects_null_byte_in_the_program_name() -> None:
    events: list[ProcessEvent] = []
    factory = _RecordingPtyFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, pty_process_factory=factory)
    manager.register_listener(events.append)

    with pytest.raises(ValueError, match=r"command\[0\]") as excinfo:
        manager.spawn_pty(["clau\x00de", "--prompt"])

    assert factory.calls == 0
    failed = _failed_events(events)
    assert len(failed) == 1, f"exactly one FAILED transition expected; got {events!r}"
    assert failed[0].record.failure_message == str(excinfo.value)
    assert manager.list_active() == []


def test_spawn_pty_rejects_null_byte_in_env_value() -> None:
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
async def test_spawn_async_rejects_null_byte_in_the_program_name() -> None:
    events: list[ProcessEvent] = []
    factory = _RecordingAsyncFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, async_process_factory=factory)
    manager.register_listener(events.append)

    with pytest.raises(ValueError, match=r"command\[0\]") as excinfo:
        await manager.spawn_async(["clau\x00de", "--prompt"])

    assert factory.calls == 0
    failed = _failed_events(events)
    assert len(failed) == 1, f"exactly one FAILED transition expected; got {events!r}"
    assert failed[0].record.failure_message == str(excinfo.value)
    assert manager.list_active() == []


@pytest.mark.asyncio
async def test_spawn_async_records_failed_transition_when_factory_raises_value_error() -> None:
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


def test_validator_accepts_argv_and_cwd_forms_popen_supports() -> None:
    validate_spawn_arguments(
        [Path("/bin/echo"), b"hello", "world"],
        cwd=Path("/tmp"),
        env={"RALPH_TOKEN": "value"},
    )


def test_validator_rejects_null_byte_inside_a_bytes_argument() -> None:
    with pytest.raises(ValueError, match="null byte") as excinfo:
        validate_spawn_arguments([b"/bin/echo", b"a\x00b"], cwd=None, env=None)

    assert "command[1]" in str(excinfo.value)


def test_validator_rejects_null_byte_inside_a_path_like_cwd() -> None:
    with pytest.raises(ValueError, match="null byte") as excinfo:
        validate_spawn_arguments(["/bin/echo"], cwd=Path("/tmp/we\x00ird"), env=None)

    assert "cwd" in str(excinfo.value)


def test_spawn_rejects_empty_command_without_launching_a_child() -> None:
    events: list[ProcessEvent] = []
    factory = _RecordingSyncFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, sync_process_factory=factory)
    manager.register_listener(events.append)

    with pytest.raises(ValueError, match="empty") as excinfo:
        manager.spawn([])

    assert factory.calls == 0, "an empty command must never reach the process factory"
    failed = _failed_events(events)
    assert len(failed) == 1, f"exactly one FAILED transition expected; got {events!r}"
    assert failed[0].record.failure_message == str(excinfo.value)


def test_spawn_invalid_argument_failure_is_also_an_os_error() -> None:
    manager = ProcessManager(policy=_QUIET_POLICY, sync_process_factory=_RecordingSyncFactory())

    with pytest.raises(OSError, match="null byte"):
        manager.spawn(["/bin/ec\x00ho", "ab"])


def test_spawn_accepts_null_byte_in_a_scrubbed_relay_control_variable() -> None:
    factory = _RecordingSyncFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, sync_process_factory=factory)

    manager.spawn(
        ["/bin/echo"],
        SpawnOptions(env={"RALPH_MCP_ACTIVITY_RELAY_CREDENTIAL": "a\x00b"}),
    )

    assert factory.calls == 1, "the scrubbed variable never reaches the child, so spawn must run"


def test_spawn_pty_accepts_null_byte_in_a_scrubbed_relay_control_variable() -> None:
    factory = _RecordingPtyFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, pty_process_factory=factory)

    manager.spawn_pty(
        ["claude"],
        PtySpawnOptions(env={"RALPH_MCP_ACTIVITY_RELAY_CREDENTIAL": "a\x00b"}),
    )

    assert factory.calls == 1


@pytest.mark.asyncio
async def test_spawn_async_accepts_null_byte_in_a_scrubbed_relay_control_variable() -> None:
    factory = _RecordingAsyncFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, async_process_factory=factory)

    await manager.spawn_async(
        ["claude"],
        SpawnOptions(env={"RALPH_MCP_ACTIVITY_RELAY_CREDENTIAL": "a\x00b"}),
    )

    assert factory.calls == 1


@pytest.mark.asyncio
async def test_spawn_async_rejects_null_byte_in_cwd() -> None:
    events: list[ProcessEvent] = []
    factory = _RecordingAsyncFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, async_process_factory=factory)
    manager.register_listener(events.append)

    with pytest.raises(ValueError, match="null byte") as excinfo:
        await manager.spawn_async(["claude"], SpawnOptions(cwd="/tmp/we\x00ird"))

    assert factory.calls == 0
    assert "cwd" in str(excinfo.value)
    assert len(_failed_events(events)) == 1


@pytest.mark.asyncio
async def test_spawn_async_rejects_null_byte_in_env_value_without_leaking_it() -> None:
    events: list[ProcessEvent] = []
    factory = _RecordingAsyncFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, async_process_factory=factory)
    manager.register_listener(events.append)

    with pytest.raises(ValueError, match="null byte") as excinfo:
        await manager.spawn_async(["claude"], SpawnOptions(env={"RALPH_TOKEN": "s3cr\x00et"}))

    assert factory.calls == 0
    message = str(excinfo.value)
    assert "RALPH_TOKEN" in message
    assert "s3cr" not in message, f"the offending value must never be echoed; got {message!r}"
    assert len(_failed_events(events)) == 1


def test_failed_spawn_log_line_carries_the_failure_message() -> None:
    lines: list[str] = []
    sink_id = logger.add(lambda message: lines.append(str(message)), level="ERROR")
    try:
        manager = ProcessManager(
            policy=ProcessManagerPolicy(
                default_grace_period_s=0.0,
                kill_followup_timeout_s=0.0,
                log_events=True,
                enable_zombie_reaper=False,
            ),
            sync_process_factory=_RecordingSyncFactory(),
        )
        with pytest.raises(ValueError, match="null byte"):
            manager.spawn(["/bin/ec\x00ho", "ab"])
    finally:
        logger.remove(sink_id)

    logged = "\n".join(lines)
    assert "command[0]" in logged, (
        f"the FAILED log line must carry the legible failure message; got {logged!r}"
    )
    assert "/bin/ec" in logged, (
        f"the FAILED log line must name the command that could not be spawned; got {logged!r}"
    )


def test_failed_spawn_log_line_never_leaks_argv_payloads() -> None:
    secret_prompt = "CONFIDENTIAL PROMPT BODY api_key=sk-live-DEADBEEF"
    mcp_config = '{"mcpServers":{"x":{"env":{"THIRD_PARTY_TOKEN":"tok-XYZ"}}}}'
    lines: list[str] = []
    sink_id = logger.add(lines.append, level="DEBUG")
    try:
        manager = ProcessManager(sync_process_factory=_RecordingSyncFactory())
        with pytest.raises(ValueError, match="null byte"):
            manager.spawn(["/bin/ec\x00ho", "--mcp-config", mcp_config, "--", secret_prompt])
    finally:
        logger.remove(sink_id)

    logged = "\n".join(lines)
    assert "/bin/ec" in logged, (
        f"the FAILED log line must still name the executable; got {logged!r}"
    )
    assert "withheld" in logged, (
        f"the FAILED log line must record that arguments were withheld; got {logged!r}"
    )
    assert secret_prompt not in logged, (
        f"the inline prompt body must never reach a log sink; got {logged!r}"
    )
    assert "tok-XYZ" not in logged, (
        f"a third-party credential in argv must never reach a log sink; got {logged!r}"
    )
