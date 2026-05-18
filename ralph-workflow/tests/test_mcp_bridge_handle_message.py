"""Tests for ralph/mcp/artifacts/bridge.py — MCP bridge layer."""

from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import MagicMock

from ralph.mcp.artifacts.bridge import (
    BridgeConfig,
    MCPBridge,
)

METHOD_NOT_FOUND_CODE = -32601
INVALID_REQUEST_CODE = -32600


def _object_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _object_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


class TestHandleMessage:
    def _make_bridge(self, transport: MagicMock | None = None) -> MCPBridge:
        config = BridgeConfig(transport=transport)
        return MCPBridge(config)

    def test_handle_message_dispatches_to_unknown_method(self) -> None:
        bridge = self._make_bridge()

        message = MagicMock()
        message.method = "unknown/method"
        message.msg_id = "1"

        result = asyncio.run(bridge.handle_message(message))
        assert result is not None
        error = _object_dict(result.error)
        assert error["code"] == METHOD_NOT_FOUND_CODE
        assert "Method not found" in cast("str", error["message"])
