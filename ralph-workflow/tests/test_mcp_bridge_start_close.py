"""Tests for ralph/mcp/artifacts/bridge.py — MCP bridge layer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ralph.mcp.artifacts.bridge import (
    BridgeConfig,
    MCPBridge,
)
from ralph.mcp.protocol.transport import StdioTransport

METHOD_NOT_FOUND_CODE = -32601
INVALID_REQUEST_CODE = -32600


def _object_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _object_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


class TestStartClose:
    def _make_bridge(self, transport: MagicMock | None = None) -> MCPBridge:
        config = BridgeConfig(transport=transport)
        return MCPBridge(config)

    def test_start_with_custom_transport(self) -> None:
        # Custom transport (not StdioTransport) - start() not called
        transport = MagicMock()
        bridge = self._make_bridge(transport=transport)
        bridge.start()
        assert bridge._running is True
        # Custom transport's start should not be called
        transport.start.assert_not_called()

    @patch.object(StdioTransport, "start")
    def test_start_sets_running_flag(self, mock_start: MagicMock) -> None:
        bridge = self._make_bridge()
        assert bridge._running is False
        bridge.start()
        assert bridge._running is True
        mock_start.assert_called_once_with()

    @patch.object(StdioTransport, "start")
    def test_start_without_stdio_transport(self, mock_start: MagicMock) -> None:
        bridge = self._make_bridge()
        # No transport specified, StdioTransport is used by default with noop command
        bridge.start()
        assert bridge._running is True
        mock_start.assert_called_once_with()

    @patch.object(MCPBridge, "start")
    async def test_run_loop(self, mock_start: MagicMock) -> None:
        transport = MagicMock()
        bridge = self._make_bridge(transport=transport)

        # Simulate one message then close
        async def mock_recv() -> object:
            yield MagicMock()  # One message

        async def mock_send(msg: object) -> None:
            pass

        transport.recv = mock_recv
        transport.send = mock_send

        bridge._running = True
        await bridge.run()
        mock_start.assert_called_once()
