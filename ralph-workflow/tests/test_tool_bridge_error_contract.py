"""The tool-dispatch boundary must not turn operational failures into retryable
protocol errors.

INCIDENT: an agent re-issued an identical failing MCP tool call for ~5 hours.
Mechanism: an operational failure inside a handler (a timeout) propagated as an
exception and was returned to the agent as a JSON-RPC ``-32603`` PROTOCOL error,
which reads as *transient/retryable* — so the agent retried forever.

Contract pinned here (single systemic guard, independent of any one handler):
- An OPERATIONAL ``ToolError`` (timeout, output-limit, spawn/IO/git failure —
  including ``ExecutionError``) becomes a ``ToolResult(is_error=True)``. It is
  terminal and non-retryable, never a ``-32603``.
- A FIX-YOUR-CALL error (``InvalidParamsError``, ``CapabilityDeniedError``) still
  propagates as a protocol error: retrying unchanged is correctly rejected, and
  the agent must change the call.
- A genuine bug (any non-``ToolError`` exception) still becomes a
  ``ToolDispatchError`` (``-32603``).
"""

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
    bridge = _bridge_with_handler(
        ExecutionError("Failed to execute 'x': output limit exceeded")
    )
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


def test_invalid_params_error_still_propagates_as_protocol_error() -> None:
    bridge = _bridge_with_handler(InvalidParamsError("'x' must be a string"))
    with pytest.raises(InvalidParamsError):
        bridge.dispatch("boom", {})


def test_capability_denied_error_still_propagates() -> None:
    bridge = _bridge_with_handler(CapabilityDeniedError("nope"))
    with pytest.raises(CapabilityDeniedError):
        bridge.dispatch("boom", {})


def test_unexpected_bug_still_becomes_dispatch_error() -> None:
    bridge = _bridge_with_handler(RuntimeError("kaboom"))
    with pytest.raises(ToolDispatchError):
        bridge.dispatch("boom", {})
