"""An unusable spawn argument must reach every layer as a structured failure.

The spawn seams in :class:`ralph.process.manager.ProcessManager` record a
FAILED ``ProcessRecord`` for a rejected spawn, but the layers ABOVE them are
what turn a spawn failure into something a caller or an agent can act on:

  * ``SubprocessAgentExecutor.run`` -> ``WorkerStatus.FAILED`` + ``ExecutorError``
  * the agent-facing ``exec`` MCP tool -> ``ExecutionError``
  * ``ralph.executor.process.run_process`` -> ``ProcessExecutionError``
  * ``ralph.git.operations`` fallbacks -> ``except OSError`` around ``run_git``

Every one of those keys on ``OSError`` -- the class the OS raises when it
cannot start a process. A NUL byte in argv or an empty argv is exactly that
condition, so :class:`ralph.process._spawn_validation.InvalidSpawnArgumentError`
is an ``OSError`` (and, for compatibility with the ``ValueError`` that
``subprocess.Popen`` raises for the same input today, a ``ValueError`` too).

These tests drive each layer with an unusable argument and assert the layer's
own structured outcome -- not the exception class it happens to catch.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.executor import ExecutorError
from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.executor.process import ProcessExecutionError, run_process
from ralph.git.subprocess_runner import run_git
from ralph.mcp.tools.exec import ExecRunDeps, ExecutionError, run_command
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus
from ralph.process.manager import ProcessManager, ProcessManagerPolicy, SpawnOptions
from ralph.testing.fake_process import FakeAsyncProcess, FakePopen, FakePsutil

if TYPE_CHECKING:
    from collections.abc import Sequence

_QUIET_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.0,
    kill_followup_timeout_s=0.0,
    log_events=False,
    enable_zombie_reaper=False,
)

#: A program name carrying a NUL: still unusable under the spawn contract,
#: where a NUL in argv[1:] is stripped instead (authored content rides there).
#: See ``ralph/process/_spawn_argv.py``.
_NUL_PROGRAM = "ec\x00ho"


def _sync_factory(command: Sequence[str], opts: SpawnOptions) -> FakePopen:
    """Sync factory that would succeed -- so only validation can fail the spawn."""
    del command, opts
    return FakePopen(pid=9001)


async def _async_factory(
    command: Sequence[str],
    *,
    cwd: str | None,
    env: dict[str, str] | None,
    stdin: int | None,
    stdout: int | None,
    stderr: int | None,
    start_new_session: bool,
) -> FakeAsyncProcess:
    """Async factory that would succeed -- so only validation can fail the spawn."""
    del command, cwd, env, stdin, stdout, stderr, start_new_session
    return FakeAsyncProcess(pid=9002)


def _manager() -> ProcessManager:
    return ProcessManager(
        policy=_QUIET_POLICY,
        psutil=FakePsutil(),
        sync_process_factory=_sync_factory,
        async_process_factory=_async_factory,
    )


async def test_agent_executor_reports_failed_and_raises_executor_error() -> None:
    """A NUL in the agent program name must not skip the status callback."""
    statuses: list[WorkerStatus] = []
    executor = SubprocessAgentExecutor(command=(_NUL_PROGRAM, "--print"), _pm=_manager())

    with pytest.raises(ExecutorError) as excinfo:
        await executor.run(
            WorkUnit(unit_id="nul-argv", description="nul-argv-test"),
            on_output=lambda _line: None,
            on_status=statuses.append,
        )

    assert "null byte" in str(excinfo.value), (
        f"the ExecutorError must carry the legible spawn diagnosis; got {excinfo.value!r}"
    )
    assert statuses and statuses[-1] is WorkerStatus.FAILED, (
        f"on_status must end on FAILED for a spawn that never started; got {statuses}"
    )


def test_exec_mcp_tool_raises_execution_error_for_a_nul_argument(tmp_path: Path) -> None:
    """Agent-supplied argv is agent-reachable, so it must honour the tool contract."""
    with pytest.raises(ExecutionError) as excinfo:
        run_command(
            _NUL_PROGRAM,
            ["hello"],
            tmp_path,
            5000,
            deps=ExecRunDeps(process_manager=_manager(), cwd_provider=lambda: tmp_path),
        )

    assert "null byte" in str(excinfo.value), (
        f"the ExecutionError must carry the legible spawn diagnosis; got {excinfo.value!r}"
    )


def test_run_process_raises_process_execution_error_for_a_nul_argument() -> None:
    """``run_process`` promises ``ProcessExecutionError`` for every spawn failure."""
    with pytest.raises(ProcessExecutionError) as excinfo:
        run_process(_NUL_PROGRAM, ["hello"], _pm=_manager())

    assert "null byte" in str(excinfo.value), (
        f"the ProcessExecutionError must carry the legible spawn diagnosis; got {excinfo.value!r}"
    )


def test_run_git_spawn_failure_stays_catchable_as_an_os_error() -> None:
    """``ralph.git.operations`` falls back to GitPython on ``except OSError``."""
    with pytest.raises(OSError, match="null byte"):
        run_git(("log",), cwd=Path("/tm\x00p"), label="git-log")
