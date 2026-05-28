"""MCP transport layer.

Provides transport abstractions for MCP communication. Supports stdio and
HTTP/SSE connections to MCP clients and servers.
"""

from __future__ import annotations

import contextlib
import json
import threading
from queue import Empty, Queue
from subprocess import PIPE as _SUBPROCESS_PIPE
from typing import IO, TYPE_CHECKING, Protocol, cast

from loguru import logger

from ralph.mcp.protocol._mcp_message import MCPMessage
from ralph.mcp.protocol._transport_error import TransportError
from ralph.process.manager import (
    ManagedProcess,
    ProcessTerminationError,
    SpawnOptions,
    get_process_manager,
)

__all__ = [
    "MCPMessage",
    "MCPTransport",
    "StdioTransport",
    "TransportError",
]

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
if TYPE_CHECKING:

    class ProcessLike(Protocol):
        """Subset of subprocess.Popen required by StdioTransport."""

        @property
        def stdin(self) -> IO[bytes] | None: ...

        @property
        def stdout(self) -> IO[bytes] | None: ...

        @property
        def stderr(self) -> IO[bytes] | None: ...

        def terminate(self) -> None: ...
        def wait(self, timeout: float | None = None) -> int: ...
        def kill(self) -> None: ...

    class ThreadLike(Protocol):
        """Minimal threading.Thread interface required by StdioTransport."""

        def start(self) -> None: ...


class StdioTransport:
    """MCP transport over stdio.

    Communicates with an MCP server process via stdin/stdout.
    Each line is a JSON-RPC message.
    """

    def __init__(
        self,
        command: list[str],
        cwd: str | None = None,
        *,
        process_factory: Callable[[list[str], str | None], ProcessLike] | None = None,
        thread_factory: Callable[[Callable[[], None], bool], ThreadLike] | None = None,
    ) -> None:
        """Initialize stdio transport.

        Args:
            command: Command and arguments to spawn as MCP server.
            cwd: Working directory for the subprocess.
        """
        self._command = command
        self._cwd = cwd
        self._process_factory = process_factory or _default_process_factory
        self._thread_factory = thread_factory or _default_thread_factory
        self._process: ProcessLike | None = None
        self._send_queue: Queue[str | dict[str, object]] = Queue()
        self._recv_queue: Queue[MCPMessage] = Queue()
        self._closed = False
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the MCP server process."""
        self._process = self._process_factory(self._command, self._cwd)
        self._reader_thread = self._thread_factory(self._read_loop, True)
        self._writer_thread = self._thread_factory(self._write_loop, True)
        self._reader_thread.start()
        self._writer_thread.start()
        logger.info("Started MCP server: {}", " ".join(self._command))

    def _read_loop(self) -> None:
        """Read from stdout in a loop."""
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
                msg_dict = cast("dict[str, object]", json.loads(stripped.decode("utf-8")))
                method_value = msg_dict.get("method")
                method = method_value if isinstance(method_value, str) else ""
                params_value = msg_dict.get("params")
                params = (
                    cast("dict[str, object] | None", params_value)
                    if isinstance(params_value, dict)
                    else None
                )
                error_value = msg_dict.get("error")
                error = (
                    cast("dict[str, object] | None", error_value)
                    if isinstance(error_value, dict)
                    else None
                )
                msg_id_value = msg_dict.get("id")
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
        """Write to stdin in a loop."""
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
        """Send a message to the MCP server."""
        if self._closed:
            raise TransportError("Transport is closed")
        msg_dict: dict[str, object] = {"method": message.method}
        if message.params is not None:
            msg_dict["params"] = message.params
        if message.msg_id is not None:
            msg_dict["id"] = message.msg_id
        self._send_queue.put(msg_dict)

    async def recv(self) -> AsyncIterator[MCPMessage]:
        """Receive messages as an async iterator."""
        while not self._closed:
            try:
                msg = self._recv_queue.get(timeout=0.1)
                yield msg
            except Empty:
                continue

    async def close(self) -> None:
        """Close the transport."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._process is not None:
            proc = self._process
            self._process = None
            if isinstance(proc, ManagedProcess):
                try:
                    proc.terminate(grace_period_s=5.0)
                except ProcessTerminationError:
                    logger.exception("Failed to close stdio transport")
                    raise
                finally:
                    for attr in ("stdin", "stdout", "stderr"):
                        pipe: IO[bytes] | None = getattr(proc, attr, None)
                        if pipe is not None:
                            with contextlib.suppress(Exception):
                                pipe.close()
            else:
                proc.terminate()
                proc.wait()
        logger.info("Closed stdio transport")


MCPTransport = StdioTransport


def _default_process_factory(command: list[str], cwd: str | None) -> ManagedProcess:
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


def _default_thread_factory(target: Callable[[], None], daemon: bool) -> threading.Thread:
    return threading.Thread(target=target, daemon=daemon)
