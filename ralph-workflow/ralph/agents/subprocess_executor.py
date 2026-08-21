"""SubprocessAgentExecutor — asyncio subprocess implementation of AgentExecutor."""

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
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.agent_install_links import install_url_for
from ralph.agents.executor import ExecutorError, WorkerResult
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
    SpawnOptions,
    get_process_manager,
)
from ralph.process.manager._process_status import _TERMINAL_STATUSES

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping, Sequence
    from contextvars import Token

    from ralph.display.activity_model import ActivityProvider
    from ralph.interrupt.asyncio_bridge import SignalBridge
    from ralph.pipeline.work_units import WorkUnit
    from ralph.process.manager._managed_async_process import ManagedAsyncProcess


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


def _binary_basename(command: Sequence[str]) -> str:
    """Extract the executable name from a spawn argv, ignoring absolute paths."""
    if not command:
        return ""
    head = command[0]
    return head.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def _missing_binary_message(command: Sequence[str]) -> str:
    """Format the 'agent binary not found' ExecutorError message.

    Returns a what/why/fix envelope pointing the operator at the missing
    CLI's install URL. The URL is sourced from ``install_url_for`` (see
    ``agent_install_links.AGENT_INSTALL_URLS``) so a newly added agent
    automatically gains an install hint here.
    """
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
    """AgentExecutor that spawns a subprocess in its own process group.

    Uses ProcessManager.spawn_async with start_new_session=True so the child
    gets its own process group, enabling escalating tree-kill on cancellation.
    Success or failure is determined by the coordinator from empirical evidence
    (artifact submission, git changes) — never from this executor's exit code.
    """

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
        _pm: ProcessManager | None = None,
    ) -> None:
        self._command = tuple(command)
        self._signal_bridge = signal_bridge
        self._cwd = cwd
        self._extra_env = extra_env
        self.activity_router = activity_router
        self._raw_overflow_root = raw_overflow_root
        # Optional watchdog-style subagent activity sink. The
        # ``ActivityRouter.push_raw_line`` path calls
        # ``invoke_subagent_sink(summary)`` from the
        # ``_activity_sink`` contextvar, so the executor registers
        # the supplied sink into that contextvar at run() time and
        # resets it on exit. This way the production
        # ``SubprocessAgentExecutor -> ActivityRouter`` path keeps
        # the watchdog's ``record_subagent_work`` channel fresh
        # without relying on tests to install the sink manually.
        self._subagent_sink = subagent_sink
        self._subagent_sink_token: Token[Callable[[str], None] | None] | None = None
        self._raw_logs: dict[str, RawOverflowLog] = {}  # bounded-accumulator-ok: drained
        self._pm = _pm

    def _get_raw_log(self, unit_id: str) -> RawOverflowLog:
        if unit_id not in self._raw_logs:
            root = self._raw_overflow_root
            if root is None:
                root = self._cwd if self._cwd is not None else Path.cwd()
            # S-8 / C4: route through the shared-by-path registry so the
            # executor's per-unit overflow log and the display's per-unit
            # overflow log are the same object. Two independently-constructed
            # writers used to share the file path but neither lock nor
            # ``_first_write`` state, leading to truncation races between
            # the two writers (the plausible source of the measured
            # 2026-08-06 NUL-hole corruption). The registry keys on the
            # resolved path so all callers share one object per path.
            self._raw_logs[unit_id] = get_or_create_raw_overflow_log(
                root,
                unit_id,
                max_bytes=DEFAULT_MAX_OVERFLOW_FILE_BYTES,
            )
        return self._raw_logs[unit_id]

    def drop_unit(self, unit_id: str) -> None:
        """Release per-unit state so long parallel sessions don't accumulate state across waves.

        Removes the unit's raw overflow log entry from ``self._raw_logs``
        so the memory the log holds (up to ``DEFAULT_MAX_OVERFLOW_FILE_BYTES``
        per unit) is released when the unit is no longer needed. Calls
        ``close()`` on the log first so any buffered tail bytes reach
        disk deterministically. Safe to call for a unit that was never
        added; it just no-ops.
        """
        raw_log = self._raw_logs.pop(unit_id, None)
        if raw_log is not None:
            raw_log.close()

    @staticmethod
    async def _cancel_wait_and_terminate(
        wait_task: asyncio.Task[int],
        handle: ManagedAsyncProcess,
    ) -> None:
        """Stop a sibling wait task before terminating its managed process."""
        wait_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await wait_task
        await handle.terminate(grace_period_s=0)

    def _record_dropped_frame(self, unit_id: str, marker: str) -> None:
        """Note a dropped oversized frame in the unit's verbatim capture.

        A frame too large for the stream buffer is dropped whole; without
        this the capture skipped straight from the frame before the gap
        to the one after it and read back as complete.

        Suppressed like every other capture write on this path
        (``_capture_evicted_line`` does the same): a capture that cannot
        be written must not take the agent's output stream down with it.
        """
        with contextlib.suppress(Exception):
            self._get_raw_log(unit_id).append(marker)

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
        # Register the watchdog-style subagent sink so the
        # ``ActivityRouter.push_raw_line -> invoke_subagent_sink``
        # path actually reaches a sink. The token is captured so the
        # finally block can reset the contextvar even when the
        # executor is cancelled mid-drain.
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
                # Only where lines are captured at all. On the
                # ``on_output`` branch nothing else is written to a raw
                # log, so passing this would create a file holding
                # markers and no agent output.
                on_dropped_frame=(
                    (lambda marker: self._record_dropped_frame(unit.unit_id, marker))
                    if self.activity_router is not None
                    else None
                ),
            ):
                stripped_bytes = raw_line.rstrip(b"\n")
                line = sanitize_display_line(stripped_bytes)

                if self.activity_router is not None:
                    raw_log = self._get_raw_log(unit.unit_id)
                    # The VERBATIM capture gets the undecorated line.
                    # ``sanitize_display_line`` is a presentation helper:
                    # it strips control sequences and truncates at 200
                    # characters with an ellipsis. Writing its output
                    # here severed every wire frame longer than that
                    # into unparseable JSON, and Ralph Workflow then read
                    # the file back and graded the run
                    # ``raw transcript corrupted``. The display below
                    # still receives the sanitized text.
                    # The agent's BYTES, undecoded. Decoding here with
                    # ``errors="replace"`` rewrote a torn multi-byte
                    # sequence to U+FFFD before the capture saw it --
                    # and a torn sequence is a byte-level fingerprint of
                    # the interleaved-write hazard the capture exists to
                    # expose, so the detector was grading a file that
                    # had already been tidied up. The display below
                    # still receives decoded, sanitized text.
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
            try:
                assert handle is not None
                # The handle.wait() inside the gather is bounded by the
                # activity-aware idle watchdog teardown (see
                # audit_activity_aware_watchdog.py — teardown_subtree is
                # enforced on every fire path) and the surrounding finally
                # block always terminates a non-terminal handle, so a
                # healthy-but-slow agent is not killed by a hard ceiling.
                # The drain_output() coroutine exits on stdout EOF.
                wait_task = asyncio.create_task(handle.wait())  # mcp-timeout-ok: idle-wd-bounded
                await asyncio.gather(drain_output(), wait_task)
            except asyncio.CancelledError:
                assert handle is not None
                await handle.terminate(grace_period_s=0)
                raise
            except BaseException:
                # ``asyncio.gather`` propagates a drain failure without
                # cancelling its sibling wait. Cancel that task before
                # termination so a parser/output fault cannot race a
                # naturally-exiting child into an EXITED record and leave its
                # process tree alive.
                assert handle is not None
                await self._cancel_wait_and_terminate(wait_task, handle)
                raise
        finally:
            if self._subagent_sink_token is not None:
                with contextlib.suppress(Exception):
                    reset_subagent_sink(self._subagent_sink_token)
                self._subagent_sink_token = None
            if handle is not None and handle.record.status not in _TERMINAL_STATUSES:
                with contextlib.suppress(Exception):
                    await handle.terminate(grace_period_s=0)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(handle.wait(), timeout=0.5)  # mcp-timeout-ok: wf-bounded

        duration_ms = int((time.monotonic() - start_time) * 1000)
        exit_code = handle.returncode if handle.returncode is not None else 0

        return WorkerResult(
            unit_id=unit.unit_id,
            exit_code=exit_code,
            final_message=last_line,
            duration_ms=duration_ms,
        )


async def drain_agent_lines(
    stream: asyncio.StreamReader,
    unit_id: str,
    *,
    on_dropped_frame: Callable[[str], None] | None = None,
) -> AsyncIterator[bytes]:
    """Yield stdout lines, surviving a frame larger than the stream buffer.

    Iterating the stream directly is not safe: ``readline`` raises
    ``ValueError`` for a line past its buffer AND discards what it
    buffered, so the oversized frame and everything queued behind it are
    lost and the reading loop dies with an error that explains none of
    that. The buffer is sized for real agent frames
    (``AGENT_STREAM_BUFFER_BYTES``); this keeps a still-larger one from
    taking the rest of the unit's output with it.

    The oversized frame is dropped WHOLE. ``readuntil`` reports how many
    bytes reach the separator, so the overflowing bytes are consumed
    deliberately and the drop can be told apart from a resume that is
    still mid-frame -- the fragment a mid-frame resume returns is not a
    wire frame, and letting it into the verbatim capture would have the
    corruption detector report it as damage the agent did.
    """
    while True:
        try:
            line = await stream.readuntil(b"\n")
        except asyncio.IncompleteReadError as exc:
            # EOF. Trailing bytes without a newline are still the
            # agent's own output.
            if exc.partial:
                yield exc.partial
            return
        except asyncio.LimitOverrunError as exc:
            dropped_bytes = exc.consumed
            completed = await _discard_oversized_frame(stream, dropped_bytes, unit_id)
            # NOTED BEFORE the EOF check. The frame is gone either way,
            # and the EOF case is the one that most needs saying: the
            # agent died mid-frame, which is exactly the stall whose
            # capture tail gets read to explain it. Returning first left
            # that case -- and only that case -- a silent gap that read
            # back as a complete capture.
            if on_dropped_frame is not None:
                on_dropped_frame(
                    _dropped_frame_marker(unit_id, dropped_bytes, at_eof=not completed)
                )
            if not completed:
                return
            continue
        yield line


def _dropped_frame_marker(unit_id: str, dropped_bytes: int, *, at_eof: bool) -> str:
    """Return the JSON-object line recording a dropped oversized frame."""
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
    """Drop one frame too large for the stream buffer. False at EOF.

    ``consumed`` is how many buffered bytes reach the separator, or the
    buffer length when no separator has arrived yet. Consuming exactly
    that much and checking whether it ended at a newline is what tells a
    completed drop apart from one still mid-frame.
    """
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
    # Still inside the frame: consume the remainder up to its newline.
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
