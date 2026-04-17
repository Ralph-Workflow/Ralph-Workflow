from __future__ import annotations

import asyncio
import contextlib
import importlib
import importlib.util
import os
import signal
import time
from collections.abc import Callable
from pathlib import Path

from ralph.agents.executor import WorkerResult
from ralph.config.enums import PHASE_DEVELOPMENT
from ralph.pipeline import checkpoint
from ralph.pipeline.effects import FanOutDevelopmentEffect
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus

_HAS_PSUTIL = importlib.util.find_spec("psutil") is not None


class _FakeDisplay:
    def emit(self, unit_id: str | None, line: str) -> None:
        del unit_id, line

    def set_status(self, unit_id: str, status: object) -> None:
        del unit_id, status


class SleeperExecutor:
    def __init__(self) -> None:
        self.pids: list[int] = []
        self.cleanup_tasks: list[asyncio.Task[int]] = []

    async def run(
        self,
        unit: WorkUnit,
        *,
        on_output: Callable[[str], None],
        on_status: Callable[[WorkerStatus], None],
    ) -> WorkerResult:
        del on_output
        on_status(WorkerStatus.RUNNING)
        start_time = time.monotonic()
        proc = await asyncio.create_subprocess_exec("sleep", "30", start_new_session=True)
        self.pids.append(proc.pid)

        try:
            await proc.wait()
        except asyncio.CancelledError:
            on_status(WorkerStatus.CANCELLED)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            self.cleanup_tasks.append(asyncio.create_task(proc.wait()))
            raise

        on_status(WorkerStatus.SUCCEEDED)
        return WorkerResult(
            unit_id=unit.unit_id,
            exit_code=proc.returncode if proc.returncode is not None else 0,
            final_message="",
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )


def _make_work_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(unit_id=unit_id, description=f"Work unit {unit_id}")


def _pid_gone(pid: int) -> bool:
    if _HAS_PSUTIL:
        psutil = importlib.import_module("psutil")
        return not bool(psutil.pid_exists(pid))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _wait_for_pids_gone(pids: list[int], timeout_s: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if all(_pid_gone(pid) for pid in pids):
            return True
        time.sleep(0.02)
    return all(_pid_gone(pid) for pid in pids)


async def _run_with_cancel(
    effect: FanOutDevelopmentEffect,
    state: PipelineState,
    executor: SleeperExecutor,
    checkpoint_path: Path,
) -> None:
    task = asyncio.create_task(
        coordinator.run_fan_out(
            effect=effect,
            executor=executor,
            display=_FakeDisplay(),  # type: ignore[arg-type]
            checkpoint_path=checkpoint_path,
            state=state,
        )
    )
    asyncio.get_running_loop().call_later(0.2, task.cancel)

    try:
        async with asyncio.timeout(1.5):
            await task
    except (TimeoutError, asyncio.CancelledError):
        pass

    if executor.cleanup_tasks:
        await asyncio.gather(*executor.cleanup_tasks, return_exceptions=True)


def test_parallel_hard_kill(tmp_path: Path) -> None:
    units = tuple(_make_work_unit(f"unit-{index}") for index in range(3))
    executor = SleeperExecutor()
    effect = FanOutDevelopmentEffect(work_units=units, max_workers=3)
    state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=units)
    checkpoint_path = tmp_path / "checkpoint.json"
    worktree_dirs = [tmp_path / unit.unit_id for unit in units]

    for worktree_dir in worktree_dirs:
        worktree_dir.mkdir()

    asyncio.run(_run_with_cancel(effect, state, executor, checkpoint_path))

    assert len(executor.pids) == 3
    assert _wait_for_pids_gone(executor.pids)

    interrupted_state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        work_units=units,
        interrupted_by_user=True,
    )
    checkpoint.save(interrupted_state, checkpoint_path)
    loaded = checkpoint.load(checkpoint_path)

    assert loaded is not None
    assert loaded.interrupted_by_user is True
    assert all(path.exists() for path in worktree_dirs)
