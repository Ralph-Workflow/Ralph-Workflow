"""``SubprocessAgentExecutor.run`` must end with the agent and reap its session.

The run awaited ``gather(drain_output(), handle.wait())`` and the drain ends
only at stdout EOF. A grandchild that inherited stdout (a backgrounded tool, an
MCP server) kept it open after the agent itself exited, so ``run()`` blocked
until that grandchild died -- and the parallel coordinator calls ``run()`` with
no timeout. A grandchild that detached from stdout let ``run()`` return, but
nothing reaped it: the agent was spawned in its own session and registered for
teardown, yet no exit path ever called ``teardown_subtree``.

These tests run real ``/bin/sh`` children (never a coding agent), so they carry
the ``subprocess_e2e`` marker.
"""

from __future__ import annotations

import asyncio
import os
import time

import psutil
import pytest

from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.pipeline.work_unit import WorkUnit

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]

#: Long past any reasonable post-exit drain grace, so a run that waits for the
#: grandchild cannot finish inside ``_RUN_DEADLINE_SECONDS``.
_GRANDCHILD_SECONDS = 60
_RUN_DEADLINE_SECONDS = 15.0
_REAP_DEADLINE_SECONDS = 3.0


def _tag(case: str) -> str:
    return f"{_GRANDCHILD_SECONDS}.{os.getpid() % 1000:03d}{case}"


def _sleepers(tag: str) -> list[psutil.Process]:
    found: list[psutil.Process] = []
    for proc in psutil.process_iter(["cmdline"]):
        if proc.info.get("cmdline") == ["/bin/sleep", tag]:
            found.append(proc)
    return found


def _survivors_after_reap(tag: str) -> list[psutil.Process]:
    deadline = time.monotonic() + _REAP_DEADLINE_SECONDS
    survivors = _sleepers(tag)
    while survivors and time.monotonic() < deadline:
        time.sleep(0.05)
        survivors = _sleepers(tag)
    return survivors


async def _run(script: str, unit_id: str) -> tuple[int, list[str]]:
    lines: list[str] = []
    executor = SubprocessAgentExecutor(["/bin/sh", "-c", script])
    result = await asyncio.wait_for(
        executor.run(
            WorkUnit(unit_id=unit_id, description="reap"),
            on_output=lines.append,
            on_status=lambda _status: None,
        ),
        timeout=_RUN_DEADLINE_SECONDS,
    )
    return result.exit_code, lines


@pytest.mark.asyncio
async def test_grandchild_holding_stdout_neither_hangs_the_run_nor_outlives_it() -> None:
    tag = _tag("1")
    try:
        exit_code, lines = await _run(
            f"/bin/sleep {tag} & echo agent-done; exit 0", "reap-stdout-holder"
        )
        assert exit_code == 0
        assert lines == ["agent-done"]
        assert _survivors_after_reap(tag) == []
    finally:
        for proc in _sleepers(tag):
            proc.kill()


@pytest.mark.asyncio
async def test_grandchild_detached_from_stdout_is_reaped_when_the_agent_exits() -> None:
    tag = _tag("2")
    try:
        exit_code, lines = await _run(
            f"/bin/sleep {tag} >/dev/null 2>&1 & echo agent-done", "reap-detached"
        )
        assert exit_code == 0
        assert lines == ["agent-done"]
        assert _survivors_after_reap(tag) == []
    finally:
        for proc in _sleepers(tag):
            proc.kill()
