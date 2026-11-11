from __future__ import annotations

import asyncio
import contextlib
import subprocess
from collections.abc import Sequence
from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog.timeout_policy import TimeoutPolicy
from ralph.agents.invoke import _process_reader
from ralph.agents.invoke._agent_run_ctx import AgentRunCtx
from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.config.models import AgentConfig
from ralph.pipeline.work_units import WorkUnit
from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.testing._fake_popen import FakePopen
from ralph.testing._process_state import ProcessState
from ralph.testing.fake_process import FakeControllableAsyncProcess, FakePsutil

if TYPE_CHECKING:
    from ralph.process.manager._spawn_options import SpawnOptions


def _make_invoke_ctx() -> AgentRunCtx:
    return AgentRunCtx(
        config=AgentConfig(cmd="fake-binary"),
        show_progress=False,
        extra_env=None,
        workspace_path=None,
        policy=TimeoutPolicy(idle_timeout_seconds=1800.0),
    )


def test_invoke_reader_spawn_passes_devnull_at_runtime(
    monkeypatch: object,
) -> None:
    captured: list[SpawnOptions] = []

    def sync_factory(command: Sequence[str], opts: SpawnOptions) -> FakePopen:
        del command
        captured.append(opts)
        return FakePopen(pid=1, state=ProcessState(returncode=0))

    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            enable_zombie_reaper=False,
            log_events=False,
        ),
        sync_process_factory=sync_factory,
    )

    monkeypatch.setattr(_process_reader, "get_process_manager", lambda: pm)

    ctx = _make_invoke_ctx()
    with contextlib.suppress(Exception):
        for _line in _process_reader._run_subprocess_and_read_lines(
            ["/bin/true"],
            ctx,
        ):
            pass

    assert len(captured) == 1, (
        f"sync factory must be invoked exactly once; got {len(captured)} calls"
    )
    opts = captured[0]
    assert opts.stdin is subprocess.DEVNULL, (
        f"invoke reader must request stdin=subprocess.DEVNULL "
        f"(INHERIT would leak Ralph's controlling-terminal stdin); "
        f"got stdin={opts.stdin!r}"
    )
    assert opts.start_new_session is True, (
        f"invoke reader must keep start_new_session=True "
        f"(gives the child its own session, no controlling TTY); "
        f"got start_new_session={opts.start_new_session!r}"
    )


def test_subprocess_executor_spawn_passes_devnull_at_runtime() -> None:

    captured: list[dict[str, object]] = []

    async def async_factory(
        command: Sequence[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> FakeControllableAsyncProcess:
        del command, cwd, env, stdout, stderr
        captured.append(
            {
                "stdin": stdin,
                "start_new_session": start_new_session,
            }
        )
        event = asyncio.Event()
        event.set()
        return FakeControllableAsyncProcess(pid=1, completion_event=event)

    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            enable_zombie_reaper=False,
            log_events=False,
        ),
        async_process_factory=async_factory,
        psutil=FakePsutil(),
    )

    executor = SubprocessAgentExecutor(
        command=["/bin/true"],
        _pm=pm,
    )
    unit = WorkUnit(unit_id="unit-runtime-1", description="runtime spawn test")

    asyncio.run(
        executor.run(
            unit,
            on_output=lambda _line: None,
            on_status=lambda _status: None,
        )
    )

    assert len(captured) == 1, (
        f"async factory must be invoked exactly once; got {len(captured)} calls"
    )
    kwargs = captured[0]
    assert kwargs["stdin"] is subprocess.DEVNULL, (
        f"SubprocessAgentExecutor.run must request stdin=subprocess.DEVNULL "
        f"(INHERIT would leak Ralph's controlling-terminal stdin); "
        f"got stdin={kwargs['stdin']!r}"
    )
    assert kwargs["start_new_session"] is True, (
        f"SubprocessAgentExecutor.run must keep start_new_session=True "
        f"(gives the child its own session, no controlling TTY); "
        f"got start_new_session={kwargs['start_new_session']!r}"
    )
