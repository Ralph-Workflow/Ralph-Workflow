"""SubprocessAgentExecutor — asyncio subprocess implementation of AgentExecutor."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from typing import TYPE_CHECKING

from ralph.agents.executor import ExecutorError, WorkerResult
from ralph.display.line_sanitizer import sanitize_display_line
from ralph.pipeline.worker_state import WorkerStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ralph.pipeline.work_units import WorkUnit


class SubprocessAgentExecutor:
    """AgentExecutor that spawns a subprocess in its own process group.

    Uses asyncio.create_subprocess_exec with start_new_session=True so
    the child gets its own process group, enabling SIGKILL of the entire
    process tree on cancellation.
    """

    async def run(
        self,
        unit: WorkUnit,
        *,
        on_output: Callable[[str], None],
        on_status: Callable[[WorkerStatus], None],
        command: Sequence[str],
    ) -> WorkerResult:
        on_status(WorkerStatus.RUNNING)
        start_time = time.monotonic()
        last_line: str = ""

        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            on_status(WorkerStatus.FAILED)
            raise ExecutorError(f"Failed to start subprocess: {exc}") from exc

        async def drain_output() -> None:
            nonlocal last_line
            assert proc.stdout is not None
            async for raw_line in proc.stdout:
                line = sanitize_display_line(raw_line.rstrip(b"\n"))
                on_output(line)
                last_line = line

        try:
            await asyncio.gather(drain_output(), proc.wait())
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            raise

        duration_ms = int((time.monotonic() - start_time) * 1000)
        exit_code = proc.returncode if proc.returncode is not None else 0

        on_status(WorkerStatus.SUCCEEDED if exit_code == 0 else WorkerStatus.FAILED)

        return WorkerResult(
            unit_id=unit.unit_id,
            exit_code=exit_code,
            final_message=last_line,
            duration_ms=duration_ms,
        )


__all__ = ["SubprocessAgentExecutor"]
