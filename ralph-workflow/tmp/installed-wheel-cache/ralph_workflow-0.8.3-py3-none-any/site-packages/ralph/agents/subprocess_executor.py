"""SubprocessAgentExecutor — asyncio subprocess implementation of AgentExecutor."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from subprocess import PIPE as _PIPE
from subprocess import STDOUT as _STDOUT
from typing import TYPE_CHECKING

from ralph.agents.executor import ExecutorError, WorkerResult
from ralph.display.activity_router import ActivityRouter, detect_provider_from_command
from ralph.display.line_sanitizer import sanitize_display_line
from ralph.display.raw_overflow import DEFAULT_MAX_OVERFLOW_FILE_BYTES, RawOverflowLog
from ralph.mcp.protocol.env import AGENT_LABEL_SCOPE_ENV
from ralph.pipeline.worker_state import WorkerStatus
from ralph.process.manager import ProcessManager, get_process_manager

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ralph.display.activity_model import ActivityProvider
    from ralph.interrupt.asyncio_bridge import SignalBridge
    from ralph.pipeline.work_units import WorkUnit


def agent_process_label(unit_id: str, env: dict[str, str] | None = None) -> str:
    """Return the full process label for the root subprocess of a work unit."""
    scope = None if env is None else env.get(str(AGENT_LABEL_SCOPE_ENV))
    if scope:
        return f"agent:{scope}:{unit_id}:root"
    return f"agent:{unit_id}:root"


def agent_process_label_prefix(unit_id: str, env: dict[str, str] | None = None) -> str:
    """Return the label prefix shared by all child processes of a work unit."""
    scope = None if env is None else env.get(str(AGENT_LABEL_SCOPE_ENV))
    if scope:
        return f"agent:{scope}:{unit_id}:"
    return f"agent:{unit_id}:"


class SubprocessAgentExecutor:
    """AgentExecutor that spawns a subprocess in its own process group.

    Uses ProcessManager.spawn_async with start_new_session=True so the child
    gets its own process group, enabling escalating tree-kill on cancellation.
    Success or failure is determined by the coordinator from empirical evidence
    (artifact submission, git changes) — never from this executor's exit code.
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
        _pm: ProcessManager | None = None,
    ) -> None:
        self._command = tuple(command)
        self._signal_bridge = signal_bridge
        self._cwd = cwd
        self._extra_env = extra_env
        self.activity_router = activity_router
        self._raw_overflow_root = raw_overflow_root
        self._raw_logs: dict[str, RawOverflowLog] = {}
        self._pm = _pm

    def _get_raw_log(self, unit_id: str) -> RawOverflowLog:
        if unit_id not in self._raw_logs:
            root = self._raw_overflow_root
            if root is None:
                root = self._cwd if self._cwd is not None else Path.cwd()
            self._raw_logs[unit_id] = RawOverflowLog(
                root, unit_id, max_bytes=DEFAULT_MAX_OVERFLOW_FILE_BYTES
            )
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
        pm = self._pm if self._pm is not None else get_process_manager()
        try:
            handle = await pm.spawn_async(
                self._command,
                cwd=str(self._cwd) if self._cwd is not None else None,
                env=env,
                stdout=_PIPE,
                stderr=_STDOUT,
                start_new_session=True,
                label=agent_process_label(unit.unit_id, env),
            )
        except OSError as exc:
            on_status(WorkerStatus.FAILED)
            raise ExecutorError(f"Failed to start subprocess: {exc}") from exc

        async def drain_output() -> None:
            nonlocal last_line
            assert handle.stdout is not None
            async for raw_line in handle.stdout:
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

        try:
            try:
                await asyncio.gather(drain_output(), handle.wait())
            except asyncio.CancelledError:
                await handle.terminate(grace_period_s=0)
                raise
        finally:
            pass

        duration_ms = int((time.monotonic() - start_time) * 1000)
        exit_code = handle.returncode if handle.returncode is not None else 0

        return WorkerResult(
            unit_id=unit.unit_id,
            exit_code=exit_code,
            final_message=last_line,
            duration_ms=duration_ms,
        )


__all__ = ["SubprocessAgentExecutor"]
