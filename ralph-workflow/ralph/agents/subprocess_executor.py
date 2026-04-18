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
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from ralph.interrupt.asyncio_bridge import SignalBridge
    from ralph.pipeline.work_units import WorkUnit


class SubprocessAgentExecutor:
    """AgentExecutor that spawns a subprocess in its own process group.

    Uses asyncio.create_subprocess_exec with start_new_session=True so
    the child gets its own process group, enabling SIGKILL of the entire
    process tree on cancellation.
    """

    def __init__(
        self,
        command: Sequence[str],
        *,
        signal_bridge: SignalBridge | None = None,
        cwd: Path | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> None:
        self._command = tuple(command)
        self._signal_bridge = signal_bridge
        self._cwd = cwd
        self._extra_env = extra_env

    async def run(
        self,
        unit: WorkUnit,
        *,
        on_output: Callable[[str], None],
        on_status: Callable[[WorkerStatus], None],
    ) -> WorkerResult:
        on_status(WorkerStatus.RUNNING)
        start_time = time.monotonic()
        last_line: str = ""

        env = {**os.environ, **self._extra_env} if self._extra_env else None
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self._cwd,
                env=env,
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

        if self._signal_bridge is not None:
            self._signal_bridge.register_pid(proc.pid)

        try:
            try:
                await asyncio.gather(drain_output(), proc.wait())
            except asyncio.CancelledError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                raise
        finally:
            if self._signal_bridge is not None:
                self._signal_bridge.deregister_pid(proc.pid)

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
