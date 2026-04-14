"""Tests for ralph/mcp/bridge.py — MCP bridge layer."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from ralph.mcp.artifacts import ArtifactExistsError, ArtifactNotFoundError
from ralph.mcp.bridge import (
    BridgeConfig,
    BridgeError,
    MCPBridge,
    MCPTool,
)

METHOD_NOT_FOUND_CODE = -32601
INVALID_REQUEST_CODE = -32600


def _object_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _object_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


class TestBridgeConfig:
    def test_default_values(self) -> None:
        config = BridgeConfig()
        assert config.artifact_dir == Path(".agent/artifacts")
        assert config.workspace_root == Path()
        assert config.transport is None

    def test_custom_values(self) -> None:
        transport = MagicMock()
        config = BridgeConfig(
            artifact_dir=Path("/tmp/artifacts"),
            workspace_root=Path("/workspace"),
            transport=transport,
        )
        assert config.artifact_dir == Path("/tmp/artifacts")
        assert config.workspace_root == Path("/workspace")
        assert config.transport is transport


class TestMCPTool:
    def test_creation(self) -> None:
        handler = MagicMock(return_value={"result": "ok"})
        tool = MCPTool(
            name="test_tool",
            description="A test tool",
            input_schema={"type": "object"},
            handler=handler,
        )
        assert tool.name == "test_tool"
        assert tool.description == "A test tool"
        assert tool.input_schema == {"type": "object"}
        assert tool.handler is handler


class TestMCPBridge:
    def _make_bridge(self, transport: MagicMock | None = None) -> MCPBridge:
        config = BridgeConfig(transport=transport)
        return MCPBridge(config)

    def test_registration(self) -> None:
        bridge = self._make_bridge()
        handler = MagicMock(return_value={"result": "ok"})
        bridge.register_tool(
            name="test_tool",
            description="A test tool",
            input_schema={"type": "object"},
            handler=handler,
        )
        assert "test_tool" in bridge._tools
        assert bridge._tools["test_tool"].name == "test_tool"

    def test_tool_called_success(self) -> None:
        bridge = self._make_bridge()
        handler = MagicMock(return_value={"result": "value"})
        bridge.register_tool(
            name="test_tool",
            description="A test tool",
            input_schema={},
            handler=handler,
        )
        result = bridge.tool_called("test_tool", {"arg": "value"})
        assert result["success"] is True
        assert result["result"] == {"result": "value"}
        handler.assert_called_once_with(arg="value")

    def test_tool_called_not_found(self) -> None:
        bridge = self._make_bridge()
        with pytest.raises(BridgeError, match="Tool 'nonexistent' not found"):
            bridge.tool_called("nonexistent", {})

    def test_tool_called_exception(self) -> None:
        bridge = self._make_bridge()
        handler = MagicMock(side_effect=RuntimeError("test error"))
        bridge.register_tool(
            name="failing_tool",
            description="A failing tool",
            input_schema={},
            handler=handler,
        )
        result = bridge.tool_called("failing_tool", {})
        assert result["success"] is False
        error = cast("str", result["error"])
        assert "test error" in error

    @patch("ralph.mcp.bridge.submit_artifact")
    def test_submit_artifact_success(self, mock_submit: MagicMock) -> None:
        bridge = self._make_bridge()
        artifact_mock = MagicMock()
        artifact_mock.to_dict.return_value = {"name": "test_artifact"}
        mock_submit.return_value = artifact_mock

        result = bridge.submit_artifact_mcp(
            name="test_artifact",
            artifact_type="code",
            content={"code": "print('hello')"},
        )
        assert result["success"] is True
        artifact = _object_dict(result["artifact"])
        assert artifact["name"] == "test_artifact"

    @patch("ralph.mcp.bridge.submit_artifact")
    def test_submit_artifact_exists(self, mock_submit: MagicMock) -> None:
        bridge = self._make_bridge()
        mock_submit.side_effect = ArtifactExistsError("Artifact already exists")

        result = bridge.submit_artifact_mcp(
            name="test_artifact",
            artifact_type="code",
            content={"code": "print('hello')"},
        )
        assert result["success"] is False
        error = cast("str", result["error"])
        assert "already exists" in error

    @patch("ralph.mcp.bridge.get_artifact")
    def test_get_artifact_success(self, mock_get: MagicMock) -> None:
        bridge = self._make_bridge()
        artifact_mock = MagicMock()
        artifact_mock.to_dict.return_value = {"name": "test_artifact"}
        mock_get.return_value = artifact_mock

        result = bridge.get_artifact_mcp("test_artifact")
        assert result["success"] is True
        artifact = _object_dict(result["artifact"])
        assert artifact["name"] == "test_artifact"

    @patch("ralph.mcp.bridge.get_artifact")
    def test_get_artifact_not_found(self, mock_get: MagicMock) -> None:
        bridge = self._make_bridge()
        mock_get.side_effect = ArtifactNotFoundError("Artifact not found")

        result = bridge.get_artifact_mcp("nonexistent")
        assert result["success"] is False
        error = cast("str", result["error"])
        assert "not found" in error

    @patch("ralph.mcp.bridge.list_artifacts")
    def test_list_artifacts_success(self, mock_list: MagicMock) -> None:
        bridge = self._make_bridge()
        artifact_mock = MagicMock()
        artifact_mock.to_dict.return_value = {"name": "artifact1"}
        mock_list.return_value = [artifact_mock]

        result = bridge.list_artifacts_mcp()
        assert result["success"] is True
        artifacts = _object_list(result["artifacts"])
        assert len(artifacts) == 1
        first_artifact = _object_dict(artifacts[0])
        assert first_artifact["name"] == "artifact1"


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

    def test_start_sets_running_flag(self) -> None:
        bridge = self._make_bridge()
        assert bridge._running is False
        bridge.start()
        assert bridge._running is True

    def test_start_without_stdio_transport(self) -> None:
        bridge = self._make_bridge()
        # No transport specified, StdioTransport is used by default with noop command
        bridge.start()
        assert bridge._running is True

    @patch.object(MCPBridge, "start")
    async def test_run_loop(self, mock_start: MagicMock) -> None:
        transport = MagicMock()
        bridge = self._make_bridge(transport=transport)

        # Simulate one message then close
        async def mock_recv():
            yield MagicMock()  # One message

        async def mock_send(msg):
            pass

        transport.recv = mock_recv
        transport.send = mock_send

        bridge._running = True
        await bridge.run()
        mock_start.assert_called_once()
