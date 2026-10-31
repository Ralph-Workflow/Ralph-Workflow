"""Once the agent exits, ``SubprocessAgentExecutor.run`` drains for a bounded grace only.

The drain used to end only at stdout EOF, so a grandchild that inherited the
agent's stdout kept ``run()`` pending for as long as it lived. The fake below
models exactly that: the agent process has exited (``wait()`` returns and the
return code is known) but stdout never reaches EOF. Everything the agent wrote
before exiting must still reach ``on_output``.

Fakes only (no real subprocess, no sleep); the real-process proof, including
that the grandchild is reaped, is ``test_subprocess_executor_reaps_session.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.pipeline.work_unit import WorkUnit
from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.testing.fake_process import FakePsutil

_EXIT_CODE = 7


class _ExitedButStdoutHeldOpen:
    """An agent that has exited while a grandchild still holds its stdout."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = _EXIT_CODE
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(b"agent-done\n")
        self.stdin = None
        self.stderr = None

    async def wait(self) -> int:
        return _EXIT_CODE

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        del input
        return b"", b""

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


@pytest.mark.asyncio
async def test_run_returns_after_the_grace_when_stdout_never_reaches_eof() -> None:
    fake = _ExitedButStdoutHeldOpen(pid=4545)

    async def _factory(
        command: list[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> object:
        del command, cwd, env, stdin, stdout, stderr, start_new_session
        return fake

    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0,
            kill_followup_timeout_s=0.0,
            log_events=False,
            enable_zombie_reaper=False,
        ),
        async_process_factory=_factory,
        psutil=FakePsutil(),
    )
    executor = SubprocessAgentExecutor(["fake-agent"], post_exit_drain_grace_seconds=0.0, _pm=pm)
    lines: list[str] = []

    result = await asyncio.wait_for(
        executor.run(
            WorkUnit(unit_id="held-stdout", description="held"),
            on_output=lines.append,
            on_status=lambda _status: None,
        ),
        timeout=1.0,
    )

    assert result.exit_code == _EXIT_CODE
    assert lines == ["agent-done"]
    assert result.final_message == "agent-done"


class _LongRunningAgent:
    """An agent that keeps running past the process manager's exit-wait cap."""

    def __init__(self, pid: int, *, runtime_s: float) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.stdout = asyncio.StreamReader()
        self.stdin = None
        self.stderr = None
        self._exited = asyncio.Event()
        asyncio.get_running_loop().call_later(runtime_s, self._exit)

    def _exit(self) -> None:
        self.stdout.feed_data(b"long-agent-done\n")
        self.stdout.feed_eof()
        self.returncode = 0
        self._exited.set()

    async def wait(self) -> int:
        await asyncio.wait_for(self._exited.wait(), timeout=5.0)
        return 0

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        del input
        return b"", b""

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


@pytest.mark.asyncio
async def test_an_agent_running_past_the_exit_wait_cap_is_not_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agent's own runtime is supervised by the idle watchdog, not capped.

    ``ManagedAsyncProcess.wait()`` bounds a post-termination exit wait at
    ``PROCESS_EXIT_WAIT_SECONDS``; using it for the whole run made every
    parallel unit that worked longer than that cap fail with ``TimeoutError``.
    """
    from ralph import timeout_defaults

    monkeypatch.setattr(timeout_defaults, "PROCESS_EXIT_WAIT_SECONDS", 0.05)
    fake = _LongRunningAgent(pid=4646, runtime_s=0.3)

    async def _factory(
        command: list[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> object:
        del command, cwd, env, stdin, stdout, stderr, start_new_session
        return fake

    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0,
            kill_followup_timeout_s=0.0,
            log_events=False,
            enable_zombie_reaper=False,
        ),
        async_process_factory=_factory,
        psutil=FakePsutil(),
    )
    executor = SubprocessAgentExecutor(["fake-agent"], post_exit_drain_grace_seconds=0.0, _pm=pm)
    lines: list[str] = []

    result = await asyncio.wait_for(
        executor.run(
            WorkUnit(unit_id="long-runner", description="long"),
            on_output=lines.append,
            on_status=lambda _status: None,
        ),
        timeout=2.0,
    )

    assert result.exit_code == 0
    assert lines == ["long-agent-done"]
