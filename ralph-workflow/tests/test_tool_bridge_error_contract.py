from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools._exec_execution_error import ExecutionError
from ralph.mcp.tools.bridge._tool_bridge import ToolBridge
from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_dispatch_error import ToolDispatchError
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata
from ralph.mcp.tools.capability_denied_error import CapabilityDeniedError
from ralph.mcp.tools.invalid_params_error import InvalidParamsError
from ralph.mcp.tools.tool_error import ToolError
from ralph.mcp.tools.tool_result import ToolResult

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._types import JsonObject


def _bridge_with_handler(raised: Exception) -> ToolBridge:
    bridge = ToolBridge()

    def _handler(
        _host_session: object | None,
        _workspace: object | None,
        _params: JsonObject,
    ) -> object:
        raise raised

    bridge.register(
        ToolMetadata(
            definition=ToolDefinition(
                name="boom", description="d", input_schema={"type": "object"}
            ),
            required_capability="ProcessExecBounded",
        ),
        _handler,
    )
    return bridge


def test_operational_execution_error_becomes_is_error_result() -> None:
    bridge = _bridge_with_handler(ExecutionError("Failed to execute 'x': output limit exceeded"))
    result = bridge.dispatch("boom", {})
    assert isinstance(result, ToolResult)
    assert result.is_error is True
    assert "output limit" in result.content[0].text


def test_operational_tool_error_becomes_is_error_result() -> None:
    bridge = _bridge_with_handler(ToolError("Failed to read file 'a': disk error"))
    result = bridge.dispatch("boom", {})
    assert isinstance(result, ToolResult)
    assert result.is_error is True
    assert "disk error" in result.content[0].text


def test_invalid_params_error_still_propagates_from_tool_bridge() -> None:
    bridge = _bridge_with_handler(InvalidParamsError("'x' must be a string"))
    with pytest.raises(InvalidParamsError):
        bridge.dispatch("boom", {})


def test_capability_denied_error_still_propagates_from_tool_bridge() -> None:
    bridge = _bridge_with_handler(CapabilityDeniedError("nope"))
    with pytest.raises(CapabilityDeniedError):
        bridge.dispatch("boom", {})


def test_unexpected_bug_still_becomes_dispatch_error() -> None:
    bridge = _bridge_with_handler(RuntimeError("kaboom"))
    with pytest.raises(ToolDispatchError):
        bridge.dispatch("boom", {})


def test_unknown_tool_error_is_actionable() -> None:
    bridge = ToolBridge()

    def _handler(
        _host_session: object | None,
        _workspace: object | None,
        _params: JsonObject,
    ) -> object:
        return None

    bridge.register(
        ToolMetadata(
            definition=ToolDefinition(
                name="exec", description="d", input_schema={"type": "object"}
            ),
            required_capability="ProcessExecBounded",
        ),
        _handler,
    )

    with pytest.raises(ToolDispatchError) as exc_info:
        bridge.get("ralph_mcp__exec")
    message = str(exc_info.value)
    assert "is not registered" in message
    assert "exec" in message
    assert "Available tools" in message
    assert "Did you mean 'exec'?" in message
