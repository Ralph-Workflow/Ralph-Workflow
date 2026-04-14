"""Tests for ralph/mcp/transport.py — MCP transport layer."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from ralph.mcp.transport import (
    MCPMessage,
    StdioTransport,
    TransportError,
)


class TestMCPMessage:
    def test_creation_minimal(self) -> None:
        msg = MCPMessage(method="test")
        assert msg.method == "test"
        assert msg.params is None
        assert msg.msg_id is None
        assert msg.error is None

    def test_creation_full(self) -> None:
        msg = MCPMessage(
            method="test",
            params={"arg": "value"},
            msg_id="123",
            error={"code": -32600, "message": "Invalid request"},
        )
        assert msg.method == "test"
        assert msg.params == {"arg": "value"}
        assert msg.msg_id == "123"
        assert msg.error == {"code": -32600, "message": "Invalid request"}


class TestStdioTransportInit:
    def test_default_initialization(self) -> None:
        transport = StdioTransport(["echo", "test"])
        assert transport._command == ["echo", "test"]
        assert transport._cwd is None
        assert transport._process is None
        assert transport._closed is False

    def test_custom_cwd(self) -> None:
        transport = StdioTransport(["echo", "test"], cwd="/tmp")
        assert transport._cwd == "/tmp"


class TestStdioTransportStart:
    @patch("subprocess.Popen")
    def test_start_spawns_process(self, mock_popen: MagicMock) -> None:
        mock_process = MagicMock()
        mock_popen.return_value = mock_process

        transport = StdioTransport(["echo", "test"])
        transport.start()

        mock_popen.assert_called_once()
        assert transport._process is mock_process
        # Reader and writer threads should be started
        assert hasattr(transport, "_reader_thread")
        assert hasattr(transport, "_writer_thread")


class TestStdioTransportSend:
    async def test_send_success(self) -> None:
        transport = StdioTransport(["echo", "test"])
        transport._closed = False

        msg = MCPMessage(method="test", params={"arg": "value"}, msg_id="1")
        await transport.send(msg)

        # Message should be in send queue
        queued = transport._send_queue.get_nowait()
        assert isinstance(queued, dict)
        assert queued["method"] == "test"
        assert queued["params"] == {"arg": "value"}
        assert queued["id"] == "1"

    async def test_send_closed_raises(self) -> None:
        transport = StdioTransport(["echo", "test"])
        transport._closed = True

        msg = MCPMessage(method="test")
        with pytest.raises(TransportError, match="Transport is closed"):
            await transport.send(msg)

    async def test_send_without_params(self) -> None:
        transport = StdioTransport(["echo", "test"])
        transport._closed = False

        msg = MCPMessage(method="test", msg_id="1")
        await transport.send(msg)

        queued = transport._send_queue.get_nowait()
        assert isinstance(queued, dict)
        assert queued["method"] == "test"
        assert "params" not in queued
        assert queued["id"] == "1"

    async def test_send_without_msg_id(self) -> None:
        transport = StdioTransport(["echo", "test"])
        transport._closed = False

        msg = MCPMessage(method="test", params={"arg": "value"})
        await transport.send(msg)

        queued = transport._send_queue.get_nowait()
        assert isinstance(queued, dict)
        assert queued["method"] == "test"
        assert queued["params"] == {"arg": "value"}
        assert "id" not in queued


class TestStdioTransportRecv:
    async def test_recv_yields_messages(self) -> None:
        transport = StdioTransport(["echo", "test"])
        transport._closed = False

        # Put a message in the recv queue
        msg = MCPMessage(method="test", params={"arg": "value"}, msg_id="1")
        transport._recv_queue.put(msg)

        # Collect messages
        messages = []
        async for m in transport.recv():
            messages.append(m)
            break  # Get just one

        assert len(messages) == 1
        assert messages[0].method == "test"


class TestStdioTransportClose:
    @patch("subprocess.Popen")
    async def test_close_terminates_process(self, mock_popen: MagicMock) -> None:
        mock_process = MagicMock()
        mock_popen.return_value = mock_process

        transport = StdioTransport(["echo", "test"])
        transport.start()
        transport._closed = False

        await transport.close()

        assert transport._closed is True
        mock_process.terminate.assert_called_once()

    @patch("subprocess.Popen")
    async def test_close_idempotent(self, mock_popen: MagicMock) -> None:
        mock_process = MagicMock()
        mock_popen.return_value = mock_process

        transport = StdioTransport(["echo", "test"])
        transport.start()
        transport._closed = False

        await transport.close()
        await transport.close()  # Second call should be no-op

        assert mock_process.terminate.call_count == 1

    @patch("subprocess.Popen")
    async def test_close_wait_timeout_kills(self, mock_popen: MagicMock) -> None:
        mock_process = MagicMock()
        mock_process.wait.side_effect = subprocess.TimeoutExpired("cmd", 5)
        mock_popen.return_value = mock_process

        transport = StdioTransport(["echo", "test"])
        transport.start()
        transport._closed = False

        await transport.close()

        mock_process.kill.assert_called_once()
