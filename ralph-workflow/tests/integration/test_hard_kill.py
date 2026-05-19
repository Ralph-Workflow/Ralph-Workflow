from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, cast

import psutil
import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.display.parallel_display import ParallelDisplay

from ralph.pipeline import checkpoint
from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.process.manager import reset_process_manager
from tests.integration.test_hard_kill_helper_sleeperexecutor import SleeperExecutor

pytestmark = pytest.mark.subprocess_e2e

_NUM_WORKERS = 3



class _FakeDisplay:
    def emit(self, unit_id: str | None, line: str) -> None:
        del unit_id, line

    def set_status(self, unit_id: str, status: object) -> None:
        del unit_id, status




def _make_work_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(
        unit_id=unit_id,
        description=f"Work unit {unit_id}",
        allowed_directories=[f"src/{unit_id}"],
    )


def _pid_gone(pid: int) -> bool:
    return not bool(psutil.pid_exists(pid))


def _wait_for_pids_gone(pids: list[int], timeout_s: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if all(_pid_gone(pid) for pid in pids):
            return True
        time.sleep(0.02)  # signal delivery requires a brief yield; Event is insufficient here
    return all(_pid_gone(pid) for pid in pids)


async def _run_with_cancel(
    effect: FanOutEffect,
    state: PipelineState,
    executor: SleeperExecutor,
    checkpoint_path: Path,
) -> None:
    task = asyncio.create_task(
        coordinator.run_fan_out(
            effect=effect,
            executor=executor,
            display=cast("ParallelDisplay", _FakeDisplay()),
            ctx=coordinator.WorkerContext(
                log=coordinator.WorkerLog(
                    log_dir=checkpoint_path.parent / "logs",
                    run_id="hard-kill-test",
                ),
            ),
        )
    )
    asyncio.get_running_loop().call_later(0.2, task.cancel)

    with contextlib.suppress(TimeoutError, asyncio.CancelledError):
        async with asyncio.timeout(1.5):
            await task


def test_parallel_hard_kill(tmp_path: Path) -> None:
    reset_process_manager()
    try:
        units = tuple(_make_work_unit(f"unit-{index}") for index in range(_NUM_WORKERS))
        executor = SleeperExecutor()
        effect = FanOutEffect(work_units=units, max_workers=_NUM_WORKERS)
        state = PipelineState(phase="development", work_units=units)
        checkpoint_path = tmp_path / "checkpoint.json"
        worktree_dirs = [tmp_path / unit.unit_id for unit in units]

        for worktree_dir in worktree_dirs:
            worktree_dir.mkdir()

        asyncio.run(_run_with_cancel(effect, state, executor, checkpoint_path))

        assert len(executor.pids) == _NUM_WORKERS
        assert _wait_for_pids_gone(executor.pids, timeout_s=1.5)

        interrupted_state = PipelineState(
            phase="development",
            work_units=units,
            interrupted_by_user=True,
        )
        checkpoint.save(interrupted_state, checkpoint_path)
        loaded = checkpoint.load(checkpoint_path)

        assert loaded is not None
        assert loaded.interrupted_by_user is True
        assert all(path.exists() for path in worktree_dirs)
    finally:
        reset_process_manager()
