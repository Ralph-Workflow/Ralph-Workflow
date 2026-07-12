"""Regression tests proving agent spawn sites detach the controlling TTY.

When an interactive agent child inherits Ralph's controlling-terminal
stdin it can put the shared TTY into raw mode and steal keystrokes.
Ralph feeds agents via a prompt FILE / argv, never via stdin, so the
two agent-spawn sites must request ``stdin=subprocess.DEVNULL`` and
keep ``start_new_session=True``.

These tests drive the spawn sites RUNTIME via recording fake factories
on a real ``ProcessManager`` and assert on the captured ``SpawnOptions``.
No real subprocess is spawned and no production-source ``ast.parse``
is used -- the assertions run against the live code paths so a
runtime refactor that changes the actual spawn options (while keeping
equivalent-looking source text elsewhere) cannot slip through.

There are two distinct seams:

  1. **PTY-less invoke reader** -- ``ralph.agents.invoke._process_reader
     ._run_subprocess_and_read_lines`` calls the module-level SINGLETON
     ``get_process_manager()`` (imported at module load time). The
     factory is sync with signature ``(Sequence[str], SpawnOptions) ->
     _SyncProcessLike``. We monkeypatch the bound name on the reader
     module so the production code's ``get_process_manager()`` call
     resolves to our injected ``ProcessManager`` and the recording
     factory captures the ``SpawnOptions`` it would have spawned.

  2. **Fan-out executor** -- ``ralph.agents.subprocess_executor
     .SubprocessAgentExecutor`` accepts an injected ``ProcessManager``
     via the underscore keyword ``_pm=`` (constructor line ~72). The
     factory is async with signature ``(command, *, cwd, env, stdin,
     stdout, stderr, start_new_session) -> _AsyncProcessLike``. The
     keyword args are destructured from the captured ``SpawnOptions``
     at ``ProcessManager.spawn_async`` (ralph/process/manager/
     _process_manager.py:629-635); we record the ``stdin`` and
     ``start_new_session`` kwargs at the factory boundary.

Both fake factories use ``FakePopen`` /
``FakeControllableAsyncProcess`` from ``ralph/testing/fake_process.py``
so no real OS process is created. ``audit_test_policy.py:331-348``
forbids ``subprocess.run`` / ``Popen`` / ``create_subprocess_exec`` in
tests; we comply by injecting the seam instead of executing it.
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
from collections.abc import Sequence
from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog.timeout_policy import TimeoutPolicy
from ralph.agents.invoke import _process_reader
from ralph.agents.invoke._agent_run_ctx import _AgentRunCtx
from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.config.models import AgentConfig
from ralph.pipeline.work_units import WorkUnit
from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.testing._fake_popen import FakePopen
from ralph.testing._process_state import ProcessState
from ralph.testing.fake_process import FakeControllableAsyncProcess, FakePsutil

if TYPE_CHECKING:
    from ralph.process.manager._spawn_options import SpawnOptions


def _make_invoke_ctx() -> _AgentRunCtx:
    """Build a minimal ``_AgentRunCtx`` that lets the reader reach spawn."""
    return _AgentRunCtx(
        config=AgentConfig(cmd="fake-binary"),
        show_progress=False,
        extra_env=None,
        workspace_path=None,
        policy=TimeoutPolicy(idle_timeout_seconds=1800.0),
    )


def test_invoke_reader_spawn_passes_devnull_at_runtime(
    monkeypatch: object,
) -> None:
    """``_run_subprocess_and_read_lines`` requests ``DEVNULL`` stdin at spawn time.

    The sync recording factory captures the actual ``SpawnOptions``
    passed to ``ProcessManager.spawn`` -- not the source text. A
    refactor that drops the ``stdin=`` kwarg entirely, or sets it to
    ``None`` (INHERIT) or ``PIPE``, fails this test.
    """
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

    # The reader module imports ``get_process_manager`` at line 75 and
    # calls it as a module-level singleton at lines 884 and 259. Rebind
    # the name on the reader module so the production code resolves
    # ``get_process_manager()`` to our injected manager.
    monkeypatch.setattr(_process_reader, "get_process_manager", lambda: pm)

    ctx = _make_invoke_ctx()
    with contextlib.suppress(Exception):
        # ``FakePopen.stdout`` defaults to None; the reader raises after
        # spawn (expected and harmless -- the assertion is on the
        # captured SpawnOptions, not on consumed lines).
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
    """``SubprocessAgentExecutor.run`` requests ``DEVNULL`` stdin at spawn time.

    ``ProcessManager.spawn_async`` destructures ``SpawnOptions`` into
    keyword arguments before calling the async factory (ralph/process/
    manager/_process_manager.py:629-635), so we record the kwargs at
    the factory boundary. A refactor that drops ``stdin=`` from the
    ``SpawnOptions(...)`` block, or sets it to ``None`` / ``PIPE``,
    fails this test.
    """

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
        # Pre-completed event so the executor's drain_output + wait
        # gather exits cleanly without the test waiting on real time.
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
