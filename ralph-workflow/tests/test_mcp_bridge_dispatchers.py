"""Tests for ralph/mcp/artifacts/bridge.py — MCP bridge layer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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


class TestDispatchers:
    def _make_bridge(self) -> MCPBridge:
        config = BridgeConfig()
        return MCPBridge(config)

    def test_dispatch_tools_list(self) -> None:
        bridge = self._make_bridge()
        handler = MagicMock(return_value={"result": "ok"})
        bridge.register_tool(
            name="test_tool",
            description="A test tool",
            input_schema={"type": "object"},
            handler=handler,
        )

        message = MagicMock()
        message.method = "tools/list"
        message.msg_id = "1"

        result = bridge._dispatch_tools_list(message)
        assert result.method == "tools/list"
        params = _object_dict(result.params)
        assert "tools" in params
        tools = _object_list(params["tools"])
        assert len(tools) == 1

    def test_dispatch_tools_call_success(self) -> None:
        bridge = self._make_bridge()
        handler = MagicMock(return_value={"result": "ok"})
        bridge.register_tool(
            name="test_tool",
            description="A test tool",
            input_schema={},
            handler=handler,
        )

        message = MagicMock()
        message.method = "tools/call"
        message.params = {"name": "test_tool", "arguments": {"arg": "value"}}
        message.msg_id = "1"

        result = bridge._dispatch_tools_call(message)
        assert result.method == "tools/call"
        params = _object_dict(result.params)
        content = _object_list(params["content"])
        first_item = _object_dict(content[0])
        assert first_item["success"] is True

    def test_dispatch_tools_call_no_params(self) -> None:
        bridge = self._make_bridge()

        message = MagicMock()
        message.method = "tools/call"
        message.params = None
        message.msg_id = "1"

        result = bridge._dispatch_tools_call(message)
        error = _object_dict(result.error)
        assert error["code"] == INVALID_REQUEST_CODE

    @patch.object(MCPBridge, "submit_artifact_mcp")
    def test_dispatch_artifacts_submit_success(self, mock_submit: MagicMock) -> None:
        bridge = self._make_bridge()
        mock_submit.return_value = {"success": True, "artifact": {"name": "test"}}

        message = MagicMock()
        message.method = "artifacts/submit"
        message.params = {
            "name": "test",
            "type": "code",
            "content": {"code": "x=1"},
        }
        message.msg_id = "1"

        result = bridge._dispatch_artifacts_submit(message)
        assert result.method == "artifacts/submit"
        params = _object_dict(result.params)
        assert params["success"] is True

    def test_dispatch_artifacts_submit_no_params(self) -> None:
        bridge = self._make_bridge()

        message = MagicMock()
        message.method = "artifacts/submit"
        message.params = None
        message.msg_id = "1"

        result = bridge._dispatch_artifacts_submit(message)
        error = _object_dict(result.error)
        assert error["code"] == INVALID_REQUEST_CODE

    @patch.object(MCPBridge, "get_artifact_mcp")
    def test_dispatch_artifacts_get(self, mock_get: MagicMock) -> None:
        bridge = self._make_bridge()
        mock_get.return_value = {"success": True, "artifact": {"name": "test"}}

        message = MagicMock()
        message.method = "artifacts/get"
        message.params = {"name": "test"}
        message.msg_id = "1"

        result = bridge._dispatch_artifacts_get(message)
        assert result.method == "artifacts/get"
        mock_get.assert_called_once_with("test")

    @patch.object(MCPBridge, "list_artifacts_mcp")
    def test_dispatch_artifacts_list(self, mock_list: MagicMock) -> None:
        bridge = self._make_bridge()
        mock_list.return_value = {"success": True, "artifacts": []}

        message = MagicMock()
        message.method = "artifacts/list"
        message.msg_id = "1"

        result = bridge._dispatch_artifacts_list(message)
        assert result.method == "artifacts/list"
        mock_list.assert_called_once()
