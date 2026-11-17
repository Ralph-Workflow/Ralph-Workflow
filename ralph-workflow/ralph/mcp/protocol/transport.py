from __future__ import annotations

import contextlib
import json
import subprocess
import threading
from queue import Empty, Queue
from subprocess import PIPE as _SUBPROCESS_PIPE
from typing import IO, TYPE_CHECKING, Final, Protocol

from loguru import logger

from ralph.mcp.protocol._mcp_message import MCPMessage
from ralph.mcp.protocol._object_dict import as_object_dict
from ralph.mcp.protocol._transport_error import TransportError
from ralph.process.manager import (
    ManagedProcess,
    ProcessTerminationError,
    ProcessTerminationIssuer,
    SpawnOptions,
    get_process_manager,
)
from ralph.process.unmanaged_terminate import kill_unmanaged, terminate_unmanaged

__all__ = [
    "MCPMessage",
    "MCPTransport",
    "StdioTransport",
    "TransportError",
]

_CLOSE_THREAD_JOIN_SECONDS: Final[float] = 2.0

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
if TYPE_CHECKING:

    class ProcessLike(Protocol):
        @property
        def stdin(self) -> IO[bytes] | None: ...

        @property
        def stdout(self) -> IO[bytes] | None: ...

        @property
        def stderr(self) -> IO[bytes] | None: ...

        def terminate(
            self,
            grace_period_s: float | None = None,
            *,
            issuer: ProcessTerminationIssuer,
        ) -> None: ...

        def wait(self, timeout: float | None = None) -> int: ...
        def kill(self, *, issuer: ProcessTerminationIssuer) -> None: ...

    class ThreadLike(Protocol):
        def start(self) -> None: ...
        def join(self, timeout: float | None = None) -> None: ...
        def is_alive(self) -> bool: ...

    class ThreadFactory(Protocol):
        def __call__(self, target: Callable[[], None], *, daemon: bool = False) -> ThreadLike: ...


class StdioTransport:
    __slots__ = (
        "_closed",
        "_command",
        "_cwd",
        "_lock",
        "_process",
        "_process_factory",
        "_reader_thread",
        "_recv_queue",
        "_send_queue",
        "_thread_factory",
        "_writer_thread",
    )

    def __init__(
        self,
        command: list[str],
        cwd: str | None = None,
        *,
        process_factory: (
            Callable[[list[str], str | None], ProcessLike | ManagedProcess] | None
        ) = None,
        thread_factory: ThreadFactory | None = None,
    ) -> None:
        self._command = command
        self._cwd = cwd
        self._process_factory = process_factory or _default_process_factory
        self._thread_factory: ThreadFactory = thread_factory or _default_thread_factory
        self._process: ProcessLike | ManagedProcess | None = None
        self._send_queue: Queue[str | dict[str, object]] = Queue()
        self._recv_queue: Queue[MCPMessage] = Queue()
        self._closed = False
        self._lock = threading.Lock()
        self._reader_thread: ThreadLike | None = None
        self._writer_thread: ThreadLike | None = None

    def start(self) -> None:
        self._process = self._process_factory(self._command, self._cwd)
        self._reader_thread = self._thread_factory(self._read_loop, daemon=True)
        self._writer_thread = self._thread_factory(self._write_loop, daemon=True)
        self._reader_thread.start()
        self._writer_thread.start()
        logger.info("Started MCP server: {}", " ".join(self._command))

    def _read_loop(self) -> None:
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        for raw_line in proc.stdout:
            if self._closed:
                break
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                loaded: object = json.loads(stripped.decode("utf-8"))
                msg_dict = as_object_dict(loaded)
                if msg_dict is None:
                    continue
                method_value = msg_dict.get("method")
                method = method_value if isinstance(method_value, str) else ""
                params_value: object | None = msg_dict.get("params")
                params = (
                    as_object_dict(params_value)
                    if isinstance(params_value, dict)
                    else None
                )
                error_value: object | None = msg_dict.get("error")
                error = (
                    as_object_dict(error_value)
                    if isinstance(error_value, dict)
                    else None
                )
                msg_id_value: object | None = msg_dict.get("id")
                msg_id = msg_id_value if isinstance(msg_id_value, str | int) else None
                msg = MCPMessage(
                    method=method,
                    params=params,
                    msg_id=msg_id,
                    error=error,
                )
                self._recv_queue.put(msg)
            except json.JSONDecodeError as exc:
                logger.warning("Failed to parse MCP message: {}", exc)

    def _write_loop(self) -> None:
        proc = self._process
        if proc is None or proc.stdin is None:
            return
        stdin = proc.stdin
        while not self._closed:
            try:
                msg = self._send_queue.get(timeout=0.1)
                line = (json.dumps(msg) + "\n").encode("utf-8")
                stdin.write(line)
                stdin.flush()
            except Empty:
                continue
            except (OSError, ValueError):
                break

    async def send(self, message: MCPMessage) -> None:
        if self._closed:
            raise TransportError("Transport is closed")
        msg_dict: dict[str, object] = {"method": message.method}
        if message.params is not None:
            msg_dict["params"] = message.params
        if message.msg_id is not None:
            msg_dict["id"] = message.msg_id
        self._send_queue.put(msg_dict)

    async def recv(self) -> AsyncIterator[MCPMessage]:
        while not self._closed:
            try:
                msg = self._recv_queue.get(timeout=0.1)
                yield msg
            except Empty:
                continue

    async def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._process is not None:
            proc = self._process
            self._process = None
            if isinstance(proc, ManagedProcess):
                try:
                    get_process_manager().terminate(
                        proc,
                        grace_period_s=5.0,
                        issuer=ProcessTerminationIssuer.MCP_SHUTDOWN,
                    )
                except ProcessTerminationError:
                    logger.exception("Failed to close stdio transport")
                    raise
                finally:
                    for pipe in (proc.stdin, proc.stdout, proc.stderr):
                        if pipe is not None:
                            with contextlib.suppress(Exception):
                                pipe.close()
            else:
                terminate_unmanaged(proc, issuer=ProcessTerminationIssuer.MCP_SHUTDOWN)
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    kill_unmanaged(proc, issuer=ProcessTerminationIssuer.MCP_SHUTDOWN)
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        proc.wait(timeout=5.0)
        self._join_transport_thread(self._reader_thread, "_reader_thread")
        self._join_transport_thread(self._writer_thread, "_writer_thread")
        logger.info("Closed stdio transport")

    def _join_transport_thread(self, thread: ThreadLike | None, label: str) -> None:
        if thread is None:
            return
        try:
            thread.join(timeout=_CLOSE_THREAD_JOIN_SECONDS)
        except (RuntimeError, OSError, AttributeError):
            logger.warning(
                "Failed to join {} during stdio transport close",
                label,
            )
            return
        try:
            still_alive = thread.is_alive()
        except (RuntimeError, OSError, AttributeError):
            logger.warning(
                "Failed to read is_alive() on {} during stdio transport close",
                label,
            )
            return
        if still_alive:
            logger.warning(
                "{} did not exit within {}s; daemon will be reaped at interpreter exit",
                label,
                _CLOSE_THREAD_JOIN_SECONDS,
            )


MCPTransport = StdioTransport


def _default_process_factory(
    command: list[str],
    cwd: str | None,
) -> ProcessLike | ManagedProcess:
    label = f"mcp-stdio:{command[0]}"
    return get_process_manager().spawn(
        command,
        SpawnOptions(
            cwd=cwd,
            stdin=_SUBPROCESS_PIPE,
            stdout=_SUBPROCESS_PIPE,
            stderr=_SUBPROCESS_PIPE,
            label=label,
        ),
    )


def _default_thread_factory(
    target: Callable[[], None], *, daemon: bool = False
) -> threading.Thread:
    return threading.Thread(
        target=target,
        daemon=daemon,
    )
