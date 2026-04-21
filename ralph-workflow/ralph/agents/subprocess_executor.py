"""SubprocessAgentExecutor — asyncio subprocess implementation of AgentExecutor."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.executor import ExecutorError, WorkerResult
from ralph.display.activity_router import ActivityRouter, detect_provider_from_command
from ralph.display.line_sanitizer import sanitize_display_line
from ralph.display.raw_overflow import RawOverflowLog
from ralph.pipeline.worker_state import WorkerStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ralph.display.activity_model import ActivityProvider
    from ralph.interrupt.asyncio_bridge import SignalBridge
    from ralph.pipeline.work_units import WorkUnit


class SubprocessAgentExecutor:
    """AgentExecutor that spawns a subprocess in its own process group.

    Uses asyncio.create_subprocess_exec with start_new_session=True so
    the child gets its own process group, enabling SIGKILL of the entire
    process tree on cancellation.
    """

    def __init__(  # noqa: PLR0913
        self,
        command: Sequence[str],
        *,
        signal_bridge: SignalBridge | None = None,
        cwd: Path | None = None,
        extra_env: Mapping[str, str] | None = None,
        activity_router: ActivityRouter | None = None,
        raw_overflow_root: Path | None = None,
    ) -> None:
        self._command = tuple(command)
        self._signal_bridge = signal_bridge
        self._cwd = cwd
        self._extra_env = extra_env
        self.activity_router = activity_router
        self._raw_overflow_root = raw_overflow_root
        self._raw_logs: dict[str, RawOverflowLog] = {}

    def _get_raw_log(self, unit_id: str) -> RawOverflowLog:
        if unit_id not in self._raw_logs:
            root = self._raw_overflow_root
            if root is None:
                root = self._cwd if self._cwd is not None else Path.cwd()
            self._raw_logs[unit_id] = RawOverflowLog(root, unit_id)
        return self._raw_logs[unit_id]

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
        activity_provider: ActivityProvider = detect_provider_from_command(list(self._command))

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

                if self.activity_router is not None:
                    raw_log = self._get_raw_log(unit.unit_id)
                    raw_log.append(line)
                    raw_ref = raw_log.relative_reference(
                        self._raw_overflow_root or self._cwd or Path.cwd()
                    )
                    for parsed_line in line.splitlines():
                        stripped_line = parsed_line.strip()
                        if not stripped_line:
                            continue
                        self.activity_router.push_raw_line(
                            unit.unit_id,
                            stripped_line,
                            provider=activity_provider,
                            raw_reference=raw_ref,
                        )
                else:
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
