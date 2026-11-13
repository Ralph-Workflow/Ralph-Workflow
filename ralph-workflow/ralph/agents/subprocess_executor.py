from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from pathlib import Path
from subprocess import DEVNULL as _DEVNULL
from subprocess import PIPE as _PIPE
from subprocess import STDOUT as _STDOUT
from typing import TYPE_CHECKING, Final

from loguru import logger

from ralph.agents.agent_install_links import install_url_for
from ralph.agents.executor import ExecutorError, WorkerResult
from ralph.agents.invoke._quota_detection import is_provider_quota_output_line
from ralph.agents.invoke._quota_exhausted_error import QuotaExhaustedError
from ralph.display.activity_router import ActivityRouter, detect_provider_from_command
from ralph.display.line_sanitizer import sanitize_display_line
from ralph.display.raw_overflow import (
    DEFAULT_MAX_OVERFLOW_FILE_BYTES,
    RawOverflowLog,
    get_or_create_raw_overflow_log,
)
from ralph.mcp.protocol.env import AGENT_LABEL_SCOPE_ENV
from ralph.mcp.server._activity_sink import (
    reset_subagent_sink,
    set_subagent_sink,
)
from ralph.pipeline.worker_state import WorkerStatus
from ralph.process.manager import (
    AGENT_STREAM_BUFFER_BYTES,
    ProcessManager,
    ProcessTerminationIssuer,
    SpawnOptions,
    get_process_manager,
)
from ralph.process.manager._process_status import _TERMINAL_STATUSES
from ralph.process.teardown import teardown_subtree, verified_child_session_pgid

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine, Mapping, Sequence
    from contextvars import Token

    from ralph.display.activity_model import ActivityProvider
    from ralph.interrupt.asyncio_bridge import SignalBridge
    from ralph.pipeline.work_units import WorkUnit
    from ralph.process.manager._managed_async_process import ManagedAsyncProcess


POST_EXIT_DRAIN_GRACE_SECONDS: Final[float] = 2.0

_EXIT_POLL_SECONDS: Final[float] = 0.05


def agent_process_label(unit_id: str, env: dict[str, str] | None = None) -> str:
    scope = None if env is None else env.get(str(AGENT_LABEL_SCOPE_ENV))
    if scope:
        return f"agent:{scope}:{unit_id}:root"
    return f"agent:{unit_id}:root"


def agent_process_label_prefix(unit_id: str, env: dict[str, str] | None = None) -> str:
    scope = None if env is None else env.get(str(AGENT_LABEL_SCOPE_ENV))
    if scope:
        return f"agent:{scope}:{unit_id}:"
    return f"agent:{unit_id}:"


def _binary_basename(command: Sequence[str]) -> str:
    if not command:
        return ""
    head = command[0]
    return head.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def _missing_binary_message(command: Sequence[str]) -> str:
    binary = _binary_basename(command)
    install_url = install_url_for(binary)
    what = (
        f"Could not find the '{binary}' CLI on PATH."
        if binary
        else "Could not find the agent CLI on PATH."
    )
    why = "Ralph Workflow needs the CLI to drive the active agent; missing executables surface as FileNotFoundError before the process can start."
    if install_url:
        fix = f"Install it from {install_url} (or change the active agent block in [agents.*] of ~/.config/ralph-workflow.toml to one you already have), then re-run."
    else:
        fix = "Either install the CLI on PATH, or change the active agent block in [agents.*] of ~/.config/ralph-workflow.toml to point at a binary you already have, then re-run."
    return f"{what}\nWHY: {why}\nFIX: {fix}"


class SubprocessAgentExecutor:
    def __init__(
        self,
        command: Sequence[str],
        *,
        signal_bridge: SignalBridge | None = None,
        cwd: Path | None = None,
        extra_env: Mapping[str, str] | None = None,
        activity_router: ActivityRouter | None = None,
        raw_overflow_root: Path | None = None,
        subagent_sink: Callable[[str], None] | None = None,
        post_exit_drain_grace_seconds: float = POST_EXIT_DRAIN_GRACE_SECONDS,
        _pm: ProcessManager | None = None,
    ) -> None:
        self._command = tuple(command)
        self._post_exit_drain_grace_seconds = post_exit_drain_grace_seconds
        self._signal_bridge = signal_bridge
        self._cwd = cwd
        self._extra_env = extra_env
        self.activity_router = activity_router
        self._raw_overflow_root = raw_overflow_root
        self._subagent_sink = subagent_sink
        self._subagent_sink_token: Token[Callable[[str], None] | None] | None = None
        self._raw_logs: dict[str, RawOverflowLog] = {}
        self._pm = _pm

    def _get_raw_log(self, unit_id: str) -> RawOverflowLog:
        if unit_id not in self._raw_logs:
            root = self._raw_overflow_root
            if root is None:
                root = self._cwd if self._cwd is not None else Path.cwd()
            self._raw_logs[unit_id] = get_or_create_raw_overflow_log(
                root,
                unit_id,
                max_bytes=DEFAULT_MAX_OVERFLOW_FILE_BYTES,
            )
        return self._raw_logs[unit_id]

    def drop_unit(self, unit_id: str) -> None:
        raw_log = self._raw_logs.pop(unit_id, None)
        if raw_log is not None:
            raw_log.close()

    async def _supervise(
        self,
        handle: ManagedAsyncProcess,
        drain: Coroutine[object, object, None],
        unit_id: str,
    ) -> None:
        session_pgid = _verified_session_pgid(handle.pid)
        drain_task: asyncio.Task[None] = asyncio.create_task(drain)
        try:
            try:
                await self._await_exit_and_drain(handle, drain_task, unit_id, session_pgid)
            except BaseException:
                await _cancel_task(drain_task)
                await handle.terminate(
                    grace_period_s=0,
                    issuer=ProcessTerminationIssuer.OPERATOR,
                )
                raise
        finally:
            if handle.record.status not in _TERMINAL_STATUSES:
                with contextlib.suppress(Exception):
                    await handle.terminate(
                        grace_period_s=0,
                        issuer=ProcessTerminationIssuer.OPERATOR,
                    )
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(handle.wait(), timeout=0.5)
            await _reap_session(handle, session_pgid)

    async def _await_exit_and_drain(
        self,
        handle: ManagedAsyncProcess,
        drain_task: asyncio.Task[None],
        unit_id: str,
        session_pgid: int | None,
    ) -> None:
        wait_task = asyncio.create_task(handle.wait_supervised())
        try:
            await _until_root_exit(handle, drain_task, wait_task)
            if not drain_task.done():
                await asyncio.wait({drain_task}, timeout=self._post_exit_drain_grace_seconds)
            if drain_task.done():
                drain_task.result()
            else:
                logger.warning(
                    "unit {unit} exited but its stdout is still held open after {grace}s; "
                    "abandoning the drain and reaping the session",
                    unit=unit_id,
                    grace=self._post_exit_drain_grace_seconds,
                )
                await _cancel_task(drain_task)
                await _reap_session(handle, session_pgid)
            await wait_task
        finally:
            await _cancel_task(wait_task)

    def _record_dropped_frame(self, unit_id: str, marker: str) -> None:
        with contextlib.suppress(Exception):
            self._get_raw_log(unit_id).append(marker)

    async def _sanitize_non_quota_output(
        self,
        handle: ManagedAsyncProcess,
        raw_line: bytes,
    ) -> str:
        line = sanitize_display_line(raw_line.rstrip(b"\n"))
        if is_provider_quota_output_line(line):
            await handle.terminate(
                grace_period_s=0,
                issuer=ProcessTerminationIssuer.QUOTA_DETECTOR,
                reason="quota_exhausted",
            )
            raise QuotaExhaustedError(_binary_basename(self._command), line)
        return line

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
        if self._subagent_sink is not None:
            self._subagent_sink_token = set_subagent_sink(self._subagent_sink)

        env = {**os.environ, **self._extra_env} if self._extra_env else None
        pm = self._pm if self._pm is not None else get_process_manager()
        handle: ManagedAsyncProcess | None = None
        try:
            handle = await pm.spawn_async(
                self._command,
                SpawnOptions(
                    cwd=str(self._cwd) if self._cwd is not None else None,
                    env=env,
                    stdin=_DEVNULL,
                    stdout=_PIPE,
                    stderr=_STDOUT,
                    start_new_session=True,
                    label=agent_process_label(unit.unit_id, env),
                ),
            )
        except OSError as exc:
            on_status(WorkerStatus.FAILED)
            if isinstance(exc, FileNotFoundError):
                raise ExecutorError(_missing_binary_message(self._command)) from exc
            raise ExecutorError(f"Failed to start subprocess: {exc}") from exc

        async def drain_output() -> None:
            nonlocal last_line
            assert handle is not None
            assert handle.stdout is not None
            async for raw_line in drain_agent_lines(
                handle.stdout,
                unit.unit_id,
                on_dropped_frame=(
                    (lambda marker: self._record_dropped_frame(unit.unit_id, marker))
                    if self.activity_router is not None
                    else None
                ),
            ):
                line = await self._sanitize_non_quota_output(handle, raw_line)
                stripped_bytes = raw_line.rstrip(b"\n")

                if self.activity_router is not None:
                    raw_log = self._get_raw_log(unit.unit_id)
                    raw_log.append_bytes(stripped_bytes)
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
            await self._supervise(handle, drain_output(), unit.unit_id)
        finally:
            if self._subagent_sink_token is not None:
                with contextlib.suppress(Exception):
                    reset_subagent_sink(self._subagent_sink_token)
                self._subagent_sink_token = None

        duration_ms = int((time.monotonic() - start_time) * 1000)
        exit_code = handle.returncode if handle.returncode is not None else 0

        return WorkerResult(
            unit_id=unit.unit_id,
            exit_code=exit_code,
            final_message=last_line,
            duration_ms=duration_ms,
        )


async def _cancel_task(task: asyncio.Task[object] | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def _verified_session_pgid(pid: int) -> int | None:
    try:
        return verified_child_session_pgid(pid)
    except Exception:
        return None


async def _until_root_exit(
    handle: ManagedAsyncProcess,
    drain_task: asyncio.Task[None],
    wait_task: asyncio.Task[int],
) -> None:
    while not wait_task.done() and handle.returncode is None:
        pending: set[asyncio.Task[None] | asyncio.Task[int]] = (
            {wait_task} if drain_task.done() else {drain_task, wait_task}
        )
        await asyncio.wait(pending, timeout=_EXIT_POLL_SECONDS, return_when=asyncio.FIRST_COMPLETED)
        if drain_task.done():
            drain_task.result()


async def _reap_session(handle: ManagedAsyncProcess, pgid: int | None) -> None:
    issuer = handle.record.label or "agent"
    with contextlib.suppress(Exception):
        await asyncio.to_thread(teardown_subtree, handle.pid, issuer=issuer, pgid=pgid)


async def drain_agent_lines(
    stream: asyncio.StreamReader,
    unit_id: str,
    *,
    on_dropped_frame: Callable[[str], None] | None = None,
) -> AsyncIterator[bytes]:
    while True:
        try:
            line = await stream.readuntil(b"\n")
        except asyncio.IncompleteReadError as exc:
            if exc.partial:
                yield exc.partial
            return
        except asyncio.LimitOverrunError as exc:
            dropped_bytes = exc.consumed
            completed = await _discard_oversized_frame(stream, dropped_bytes, unit_id)
            if on_dropped_frame is not None:
                on_dropped_frame(
                    _dropped_frame_marker(unit_id, dropped_bytes, at_eof=not completed)
                )
            if not completed:
                return
            continue
        yield line


def _dropped_frame_marker(unit_id: str, dropped_bytes: int, *, at_eof: bool) -> str:
    payload: dict[str, str | int | bool] = {
        "type": "ralph.capture.dropped_frame",
        "unit_id": unit_id,
        "at_least_bytes": dropped_bytes,
        "limit_bytes": AGENT_STREAM_BUFFER_BYTES,
        "at_eof": at_eof,
        "reason": (
            "the agent's output ended inside a line that exceeded the stream "
            "buffer; the frame is incomplete and nothing follows it"
            if at_eof
            else "one line exceeded the stream buffer and was dropped whole; "
            "the capture resumes at the next frame"
        ),
    }
    return json.dumps(payload)


async def _discard_oversized_frame(
    stream: asyncio.StreamReader,
    consumed: int,
    unit_id: str,
) -> bool:
    logger.warning(
        "unit {unit} emitted a line larger than the {cap}-byte stream buffer; "
        "that frame is dropped and capture resumes at the next one",
        unit=unit_id,
        cap=AGENT_STREAM_BUFFER_BYTES,
    )
    try:
        chunk = await stream.readexactly(consumed)
    except asyncio.IncompleteReadError:
        return False
    if chunk.endswith(b"\n"):
        return True
    while True:
        try:
            await stream.readuntil(b"\n")
        except asyncio.IncompleteReadError:
            return False
        except asyncio.LimitOverrunError as exc:
            try:
                await stream.readexactly(exc.consumed)
            except asyncio.IncompleteReadError:
                return False
            continue
        return True


__all__ = ["SubprocessAgentExecutor", "drain_agent_lines"]
