from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from ralph.agents.executor import WorkerResult
from ralph.agents.subprocess_executor import agent_process_label
from ralph.pipeline.worker_state import WorkerStatus
from ralph.process.manager import SpawnOptions, get_process_manager

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.work_units import WorkUnit

class SleeperExecutor:
    def __init__(self) -> None:
        self.pids: list[int] = []

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
        handle = await get_process_manager().spawn_async(
            ["sleep", "30"], SpawnOptions(label=agent_process_label(unit.unit_id))
        )
        self.pids.append(handle.record.pid)

        try:
            await asyncio.wait_for(handle.wait(), timeout=60.0)
        except TimeoutError as exc:
            raise RuntimeError("Process wait timed out unexpectedly") from exc
        except asyncio.CancelledError:
            on_status(WorkerStatus.CANCELLED)
            await asyncio.shield(handle.terminate(grace_period_s=0))
            raise

        on_status(WorkerStatus.SUCCEEDED)
        return WorkerResult(
            unit_id=unit.unit_id,
            exit_code=handle.record.returncode if handle.record.returncode is not None else 0,
            final_message="",
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )
