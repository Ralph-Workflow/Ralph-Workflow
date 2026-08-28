"""Regression coverage for embedded NUL characters in spawn arguments.

``execve`` carries argv tokens, environment entries and the child's cwd as
NUL-terminated C strings, so CPython raises ``ValueError: embedded null
byte`` before the fork when any of them contains one. Ralph passes content
it does not author as argv: the positional agent prompt (Pi, Cursor, Kimi)
carries a git diff, and ``git commit -m`` carries an agent-authored
message. A single source file with a literal NUL in a string literal put
one in the diff, into the prompt, and aborted the Pi invocation with a
``ValueError`` that named neither the token nor the run.

Every Ralph child is spawned through ``ProcessManager``, so these tests
pin the strip at that one chokepoint, through its public surface.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.process import (
    ProcessManager,
    ProcessManagerPolicy,
    ProcessRecord,
    ProcessStatus,
    PtySpawnOptions,
    SpawnOptions,
)
from ralph.testing.fake_process import (
    FakeAsyncProcess,
    FakePopen,
    FakePsutil,
    make_async_process_factory,
)
from tests._process_manager_pty_helper__fakeptyfactory import _FakePtyFactory

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.1,
    kill_followup_timeout_s=0.1,
    log_events=False,
    enable_zombie_reaper=False,
)

_NUL = "\x00"
_DIFF_PROMPT = f'+  const masked = token.replace(/x/g, () => "{_NUL}".repeat(2));'
_CLEAN_PROMPT = '+  const masked = token.replace(/x/g, () => "".repeat(2));'


def _reject_embedded_nul(values: Iterable[str]) -> None:
    """Fail exactly as CPython's ``Popen`` does on an un-encodable argument."""
    for value in values:
        if _NUL in value:
            msg = "embedded null byte"
            raise ValueError(msg)


class _ExecveFaithfulSyncFactory:
    """Sync process factory that rejects NULs the way the real spawn does."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], SpawnOptions]] = []
        self._pids = itertools.count(1000)

    def __call__(self, command: Sequence[str], opts: SpawnOptions) -> FakePopen:
        _reject_embedded_nul(command)
        if opts.cwd is not None:
            _reject_embedded_nul([opts.cwd])
        if opts.env is not None:
            _reject_embedded_nul([*opts.env.keys(), *opts.env.values()])
        self.calls.append((tuple(command), opts))
        return FakePopen(pid=next(self._pids))


def _manager(factory: _ExecveFaithfulSyncFactory) -> ProcessManager:
    return ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=factory,
        async_process_factory=make_async_process_factory(itertools.count(3000)),
        psutil=FakePsutil(),
    )


def test_spawn_strips_embedded_nul_from_a_positional_prompt_argument() -> None:
    """A NUL inside a prompt argv token must not abort the spawn."""
    factory = _ExecveFaithfulSyncFactory()
    pm = _manager(factory)

    handle = pm.spawn(["pi", "--mode", "json", _DIFF_PROMPT], SpawnOptions(label="invoke:pi"))

    spawned_command, _ = factory.calls[0]
    assert spawned_command == ("pi", "--mode", "json", _CLEAN_PROMPT)
    assert handle.record.command == ("pi", "--mode", "json", _CLEAN_PROMPT)


def test_spawn_strips_embedded_nul_from_an_agent_authored_commit_message() -> None:
    """The other argv carrier of authored content is a ``git commit -m`` subject."""
    factory = _ExecveFaithfulSyncFactory()
    pm = _manager(factory)

    pm.spawn(
        ["git", "commit", "-m", f"fix: drop the {_NUL} sentinel"],
        SpawnOptions(label="git:commit"),
    )

    spawned_command, _ = factory.calls[0]
    assert spawned_command == ("git", "commit", "-m", "fix: drop the  sentinel")


def test_spawn_rejects_an_embedded_nul_in_an_environment_entry() -> None:
    """Env carries control values, not authored content: a NUL there is a defect."""
    factory = _ExecveFaithfulSyncFactory()
    pm = _manager(factory)

    with pytest.raises(ValueError, match="RALPH_RUN"):
        pm.spawn(["git", "status"], SpawnOptions(env={f"RALPH_RUN{_NUL}": "1"}, label="git:status"))

    assert factory.calls == []


def test_spawn_rejects_an_embedded_nul_in_the_program_name() -> None:
    """argv[0] names the program: stripping it would run a DIFFERENT binary."""
    factory = _ExecveFaithfulSyncFactory()
    pm = _manager(factory)

    with pytest.raises(OSError, match=r"command\[0\]"):
        pm.spawn([f"/bin/ec{_NUL}ho", "hi"], SpawnOptions(label="echo"))

    assert factory.calls == []


def test_spawn_rejects_an_embedded_nul_in_an_environment_value() -> None:
    """The value carries the NUL as readily as the name does."""
    factory = _ExecveFaithfulSyncFactory()
    pm = _manager(factory)

    with pytest.raises(ValueError, match="RALPH_RUN"):
        pm.spawn(["git", "status"], SpawnOptions(env={"RALPH_RUN": f"run{_NUL}1"}, label="git"))

    assert factory.calls == []


def test_spawn_rejects_an_embedded_nul_in_the_working_directory() -> None:
    """Silently rewriting a path could point the child at a different directory."""
    factory = _ExecveFaithfulSyncFactory()
    pm = _manager(factory)

    with pytest.raises(ValueError, match="cwd contains"):
        pm.spawn(["git", "status"], SpawnOptions(cwd=f"/work/space{_NUL}", label="git:status"))

    assert factory.calls == []


def test_a_rejected_spawn_argument_still_records_a_failed_process() -> None:
    """A pre-fork rejection leaves the same truthful bookkeeping as an OSError."""
    factory = _ExecveFaithfulSyncFactory()
    pm = _manager(factory)
    seen: list[ProcessRecord] = []
    pm.register_listener(lambda event: seen.append(event.record))

    with pytest.raises(ValueError):
        pm.spawn(["git", "status"], SpawnOptions(cwd=f"/work{_NUL}", label="git:status"))

    assert [record.status for record in seen] == [ProcessStatus.FAILED]
    assert "embedded null byte" in (seen[0].failure_message or "")


def test_spawn_warns_when_it_strips_a_nul_and_stays_silent_otherwise() -> None:
    """The warning is the only evidence of the mutation, so it is part of the contract."""
    factory = _ExecveFaithfulSyncFactory()
    pm = _manager(factory)
    warnings: list[str] = []
    sink_id = logger.add(lambda message: warnings.append(str(message)), level="WARNING")
    try:
        pm.spawn(["pi", _DIFF_PROMPT], SpawnOptions(label="invoke:pi"))
        assert len(warnings) == 1
        assert "argv[1]: 1" in warnings[0]
        assert "invoke:pi" in warnings[0]

        pm.spawn(["pi", _CLEAN_PROMPT], SpawnOptions(label="invoke:pi"))
        assert len(warnings) == 1
    finally:
        logger.remove(sink_id)


def test_spawn_without_a_nul_passes_command_and_options_through_unchanged() -> None:
    """Clean input is never rewritten: same values, same options object."""
    factory = _ExecveFaithfulSyncFactory()
    pm = _manager(factory)
    opts = SpawnOptions(cwd="/work/space", env={"KEEP": "kept"}, label="git:status")

    pm.spawn(["git", "status", "--porcelain"], opts)

    spawned_command, spawned_opts = factory.calls[0]
    assert spawned_command == ("git", "status", "--porcelain")
    assert spawned_opts == opts


def test_spawn_pty_strips_embedded_nul_from_the_command() -> None:
    """The PTY transport shares the argv contract with the pipe transport."""
    factory = _FakePtyFactory()
    pm = ProcessManager(policy=_FAST_POLICY, pty_process_factory=factory, psutil=FakePsutil())

    handle = pm.spawn_pty(["claude", _DIFF_PROMPT], PtySpawnOptions(cwd="/work/space"))

    spawned_command, spawned_cwd, _ = factory.calls[0]
    assert spawned_command == ("claude", _CLEAN_PROMPT)
    assert spawned_cwd == "/work/space"
    assert handle.record.command == ("claude", _CLEAN_PROMPT)


async def test_spawn_async_strips_embedded_nul_from_the_command() -> None:
    """The async transport shares the argv contract with the sync one."""
    calls: list[tuple[str, ...]] = []
    async_factory = make_async_process_factory(itertools.count(4000))

    async def recording_factory(
        command: Sequence[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> FakeAsyncProcess:
        _reject_embedded_nul(command)
        calls.append(tuple(command))
        return await async_factory(
            command,
            cwd=cwd,
            env=env,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            start_new_session=start_new_session,
        )

    pm = ProcessManager(
        policy=_FAST_POLICY,
        async_process_factory=recording_factory,
        psutil=FakePsutil(),
    )

    handle = await pm.spawn_async(["pi", _DIFF_PROMPT])

    assert calls == [("pi", _CLEAN_PROMPT)]
    assert handle.record.command == ("pi", _CLEAN_PROMPT)
