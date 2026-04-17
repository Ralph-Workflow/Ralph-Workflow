import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from ralph.agents.executor import ExecutorError, WorkerResult
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus


@dataclass
class FakeRun:
    outputs: list[str]
    exit_code: int
    duration_ms: int
    raise_on_start: Exception | None = None


class FakeAgentExecutor:
    def __init__(self, runs: dict[str, FakeRun]) -> None:
        self._runs = runs
        self.calls: list[WorkUnit] = []
        self.outputs_emitted: dict[str, list[str]] = {}
        self.statuses_emitted: dict[str, list[WorkerStatus]] = {}

    async def run(
        self,
        unit: WorkUnit,
        *,
        on_output: Callable[[str], None],
        on_status: Callable[[WorkerStatus], None],
    ) -> WorkerResult:
        self.calls.append(unit)
        seed = self._runs.get(unit.unit_id)
        if seed is None:
            raise ExecutorError(f"No FakeRun seeded for unit_id={unit.unit_id!r}")

        if seed.raise_on_start is not None:
            raise seed.raise_on_start

        emitted_outputs: list[str] = []
        emitted_statuses: list[WorkerStatus] = []

        on_status(WorkerStatus.RUNNING)
        emitted_statuses.append(WorkerStatus.RUNNING)

        for line in seed.outputs:
            on_output(line)
            emitted_outputs.append(line)
            await asyncio.sleep(0)

        final_status = WorkerStatus.SUCCEEDED if seed.exit_code == 0 else WorkerStatus.FAILED
        on_status(final_status)
        emitted_statuses.append(final_status)

        self.outputs_emitted[unit.unit_id] = emitted_outputs
        self.statuses_emitted[unit.unit_id] = emitted_statuses

        final_message = seed.outputs[-1] if seed.outputs else ""
        return WorkerResult(
            unit_id=unit.unit_id,
            exit_code=seed.exit_code,
            final_message=final_message,
            duration_ms=seed.duration_ms,
        )


__all__ = ["FakeAgentExecutor", "FakeRun"]
