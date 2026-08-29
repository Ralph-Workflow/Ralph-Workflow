"""Unusable spawn arguments must fail closed with a legible, structured error.

``subprocess.Popen`` raises ``ValueError("embedded null byte")`` from
CPython's ``_fork_exec`` when argv, env, or cwd contains ``"\\x00"``, and
``IndexError`` when argv is empty. Neither message names the process or
the offending argument, and neither is an ``OSError``, so the spawn
seams' failure bookkeeping was skipped entirely -- no FAILED
``ProcessRecord`` was built and no lifecycle transition was emitted --
and every downstream layer that turns a spawn failure into a structured
outcome (``ExecutorError``, ``ExecutionError``,
``ProcessExecutionError``, the GitPython fallbacks) keys on ``OSError``
and was bypassed as well. The pipeline's blanket
``except Exception`` in ``effect_executor`` still absorbed the escape
into ``PipelineEvent.AGENT_FAILURE``, so the run did not hard-crash; what
was lost was the diagnosis.

These tests pin the observable post-fix contract for the sync, PTY and
async spawn seams:

  * a NUL byte in the program name (argv[0]), cwd, or the environment map
    that actually reaches the child, and an empty argv, are rejected
    BEFORE the process factory runs -- rewriting any of those would run
    something other than what the caller asked for;
  * a NUL byte in argv[1:] is STRIPPED instead, because that is where
    authored content rides (an agent prompt carrying a git diff of a
    source file with a literal NUL), and aborting the phase over a byte
    the child would have read happily helps nobody;
  * the raised error names the offending argument and never echoes an
    environment value, which can hold a credential;
  * the raised error is an ``OSError`` as well as a ``ValueError``, so
    downstream spawn-failure handlers see it;
  * a FAILED lifecycle transition reaches every registered listener with
    that same legible message on the record.

Note on the PTY seam: before this validation existed, a NUL argv there
never produced a parent-side ``ValueError`` at all --
``ralph.process.pty.spawn_pty_process`` forks first and calls
``os.execvpe`` in the child under ``except BaseException: os._exit(127)``,
so the failure surfaced as a child exiting 127 with no diagnosis.
Rejecting before the fork changes that path deliberately.

A factory that raises ``ValueError`` for any other reason is recorded
the same way, so the seam can no longer drop a spawn failure on the
floor because of the exception's class.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.process._spawn_validation import validate_spawn_arguments
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


def test_spawn_rejects_null_byte_in_the_program_name_without_launching_a_child() -> None:
    """A NUL byte in argv[0] must fail before the sync factory runs.

    argv[0] names the program: stripping it would launch a DIFFERENT binary
    than the caller asked for, so it is rejected like cwd and env. argv[1:]
    is stripped instead — see the companion test below and
    ``tests/process/test_spawn_nul_sanitization.py`` for why.
    """
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
    """argv[1:] carries authored content: an agent prompt holding a git diff of
    a source file with a literal NUL must not abort the phase."""
    events: list[ProcessEvent] = []
    factory = _RecordingSyncFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, sync_process_factory=factory)
    manager.register_listener(events.append)

    handle = manager.spawn(["/bin/echo", "hello\x00world"])

    assert factory.calls == 1
    assert handle.record.command == ("/bin/echo", "helloworld")
    assert _failed_events(events) == []


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
    assert "a\x00b" not in message, (
        f"the offending value must never be interpolated into the message; got {message!r}"
    )
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


def test_spawn_pty_rejects_null_byte_in_the_program_name() -> None:
    """The PTY seam rejects a NUL program name before the factory runs."""
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
async def test_spawn_async_rejects_null_byte_in_the_program_name() -> None:
    """The async seam rejects a NUL program name before the factory runs."""
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


def test_validator_accepts_argv_and_cwd_forms_popen_supports() -> None:
    """``os.PathLike`` and ``bytes`` arguments must pass, exactly as ``Popen`` accepts them."""
    validate_spawn_arguments(
        [Path("/bin/echo"), b"hello", "world"],
        cwd=Path("/tmp"),
        env={"RALPH_TOKEN": "value"},
    )


def test_validator_rejects_null_byte_inside_a_bytes_argument() -> None:
    """A NUL inside a ``bytes`` argv element is still unusable and must be named."""
    with pytest.raises(ValueError, match="null byte") as excinfo:
        validate_spawn_arguments([b"/bin/echo", b"a\x00b"], cwd=None, env=None)

    assert "command[1]" in str(excinfo.value)


def test_validator_rejects_null_byte_inside_a_path_like_cwd() -> None:
    """A NUL inside a ``Path`` working directory is still unusable and must be named."""
    with pytest.raises(ValueError, match="null byte") as excinfo:
        validate_spawn_arguments(["/bin/echo"], cwd=Path("/tmp/we\x00ird"), env=None)

    assert "cwd" in str(excinfo.value)


def test_spawn_rejects_empty_command_without_launching_a_child() -> None:
    """An empty argv names no executable and must fail closed, not raise ``IndexError``."""
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
    """Every layer that structures spawn failures keys on ``OSError``; so must this one."""
    manager = ProcessManager(policy=_QUIET_POLICY, sync_process_factory=_RecordingSyncFactory())

    with pytest.raises(OSError, match="null byte"):
        manager.spawn(["/bin/ec\x00ho", "ab"])


def test_spawn_accepts_null_byte_in_a_scrubbed_relay_control_variable() -> None:
    """A variable removed before the child exists can never poison the child."""
    factory = _RecordingSyncFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, sync_process_factory=factory)

    manager.spawn(
        ["/bin/echo"],
        SpawnOptions(env={"RALPH_MCP_ACTIVITY_RELAY_CREDENTIAL": "a\x00b"}),
    )

    assert factory.calls == 1, "the scrubbed variable never reaches the child, so spawn must run"


def test_spawn_pty_accepts_null_byte_in_a_scrubbed_relay_control_variable() -> None:
    """The PTY seam agrees with the sync seam about the scrubbed map."""
    factory = _RecordingPtyFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, pty_process_factory=factory)

    manager.spawn_pty(
        ["claude"],
        PtySpawnOptions(env={"RALPH_MCP_ACTIVITY_RELAY_CREDENTIAL": "a\x00b"}),
    )

    assert factory.calls == 1


@pytest.mark.asyncio
async def test_spawn_async_accepts_null_byte_in_a_scrubbed_relay_control_variable() -> None:
    """The async seam agrees with the sync seam about the scrubbed map."""
    factory = _RecordingAsyncFactory()
    manager = ProcessManager(policy=_QUIET_POLICY, async_process_factory=factory)

    await manager.spawn_async(
        ["claude"],
        SpawnOptions(env={"RALPH_MCP_ACTIVITY_RELAY_CREDENTIAL": "a\x00b"}),
    )

    assert factory.calls == 1


@pytest.mark.asyncio
async def test_spawn_async_rejects_null_byte_in_cwd() -> None:
    """The async seam validates the working directory, not only argv."""
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
    """The async seam validates the environment map, and never echoes the value."""
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
    """The default listener must log why the spawn failed, not just ``rc=None``."""
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
    """The FAILED log line must name the executable without echoing its arguments.

    Agent argv carries two classes of secret in ordinary operation: the inline
    prompt body appended as the trailing argument, and third-party credentials
    embedded in the ``--mcp-config`` JSON. ``ralph.agents.invoke._commands``
    already redacts the former before logging an invocation; a FAILED spawn
    must not reintroduce the leak through a different sink.
    """
    secret_prompt = "CONFIDENTIAL PROMPT BODY api_key=sk-live-DEADBEEF"
    mcp_config = '{"mcpServers":{"x":{"env":{"THIRD_PARTY_TOKEN":"tok-XYZ"}}}}'
    lines: list[str] = []
    sink_id = logger.add(lines.append, level="DEBUG")
    try:
        manager = ProcessManager(sync_process_factory=_RecordingSyncFactory())
        with pytest.raises(ValueError, match="null byte"):
            manager.spawn(["/bin/echo", "--mcp-config", mcp_config, "--", secret_prompt, "a\x00b"])
    finally:
        logger.remove(sink_id)

    logged = "\n".join(lines)
    assert "/bin/echo" in logged, (
        f"the FAILED log line must still name the executable; got {logged!r}"
    )
    assert secret_prompt not in logged, (
        f"the inline prompt body must never reach a log sink; got {logged!r}"
    )
    assert "tok-XYZ" not in logged, (
        f"a third-party credential in argv must never reach a log sink; got {logged!r}"
    )
