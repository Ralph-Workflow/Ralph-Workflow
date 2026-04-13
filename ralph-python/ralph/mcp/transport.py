"""MCP transport layer.

Provides transport abstractions for MCP communication. Supports stdio and
HTTP/SSE connections to MCP clients and servers.
"""

from __future__ import annotations

import json
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from queue import Empty, Queue
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from loguru import logger


class TransportError(Exception):
    """Raised when transport operations fail."""

    pass


@dataclass
class MCPMessage:
    """Represents an MCP message."""

    method: str
    params: dict[str, Any] | None = None
    msg_id: str | None = None
    error: dict[str, Any] | None = None


class MCPTransport(ABC):
    """Abstract base class for MCP transports."""

    @abstractmethod
    async def send(self, message: MCPMessage) -> None:
        """Send a message."""
        pass

    @abstractmethod
    async def recv(self) -> AsyncIterator[MCPMessage]:
        """Receive messages as an async iterator."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close the transport."""
        pass


class StdioTransport(MCPTransport):
    """MCP transport over stdio.

    Communicates with an MCP server process via stdin/stdout.
    Each line is a JSON-RPC message.
    """

    def __init__(self, command: list[str], cwd: str | None = None) -> None:
        """Initialize stdio transport.

        Args:
            command: Command and arguments to spawn as MCP server.
            cwd: Working directory for the subprocess.
        """
        self._command = command
        self._cwd = cwd
        self._process: subprocess.Popen[bytes] | None = None
        self._send_queue: Queue[str | dict[str, Any]] = Queue()
        self._recv_queue: Queue[MCPMessage] = Queue()
        self._closed = False
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the MCP server process."""
        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self._cwd,
        )
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._writer_thread = threading.Thread(target=self._write_loop, daemon=True)
        self._reader_thread.start()
        self._writer_thread.start()
        logger.info("Started MCP server: {}", " ".join(self._command))

    def _read_loop(self) -> None:
        """Read from stdout in a loop."""
        assert self._process is not None
        assert self._process.stdout is not None
        for raw_line in self._process.stdout:
            if self._closed:
                break
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                msg_dict = json.loads(stripped.decode("utf-8"))
                msg = MCPMessage(
                    method=msg_dict.get("method", ""),
                    params=msg_dict.get("params"),
                    msg_id=msg_dict.get("id"),
                    error=msg_dict.get("error"),
                )
                self._recv_queue.put(msg)
            except json.JSONDecodeError as exc:
                logger.warning("Failed to parse MCP message: {}", exc)

    def _write_loop(self) -> None:
        """Write to stdin in a loop."""
        assert self._process is not None
        assert self._process.stdin is not None
        while not self._closed:
            try:
                msg = self._send_queue.get(timeout=0.1)
                line = (json.dumps(msg) + "\n").encode("utf-8")
                self._process.stdin.write(line)
                self._process.stdin.flush()
            except Empty:
                continue
            except BrokenPipeError:
                break

    async def send(self, message: MCPMessage) -> None:
        """Send a message to the MCP server."""
        if self._closed:
            raise TransportError("Transport is closed")
        msg_dict: dict[str, Any] = {"method": message.method}
        if message.params is not None:
            msg_dict["params"] = message.params
        if message.msg_id is not None:
            msg_dict["id"] = message.msg_id
        self._send_queue.put(msg_dict)

    async def recv(self) -> AsyncIterator[MCPMessage]:  # type: ignore[override]
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
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        logger.info("Closed stdio transport")
