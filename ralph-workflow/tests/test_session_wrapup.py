"""Tests for the graduated session soft wrap-up nag.

Once an invocation passes the soft threshold (50 min by default), MCP tool
results carry a wrap-up banner telling the agent to finish up before the hard
force-cut (55 min). This is the "nag, then cut" behavior requested for the
session ceiling.
"""

from __future__ import annotations

from typing import Any, cast

from ralph.agents.timeout_clock import FakeClock
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.server._session_wrapup import SessionWrapupBudget, wrapup_notice
from ralph.mcp.tools.bridge import ToolBridge
from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata


def test_no_notice_before_soft_threshold() -> None:
    assert wrapup_notice(elapsed_seconds=100.0, soft_seconds=3000.0, hard_seconds=3300.0) is None


def test_notice_appears_after_soft_threshold_with_remaining_minutes() -> None:
    notice = wrapup_notice(elapsed_seconds=3060.0, soft_seconds=3000.0, hard_seconds=3300.0)
    assert notice is not None
    assert "declare_complete" in notice
    # 3300 - 3060 = 240s remaining -> ~4 minutes.
    assert "4 min" in notice


def test_disabled_soft_threshold_never_notices() -> None:
    assert wrapup_notice(elapsed_seconds=10_000.0, soft_seconds=None, hard_seconds=3300.0) is None


def test_budget_uses_injected_clock_and_start() -> None:
    clock = FakeClock()
    budget = SessionWrapupBudget(clock, soft_seconds=3000.0, hard_seconds=3300.0)
    assert budget.notice() is None
    clock.advance(3000.0)
    notice = budget.notice()
    assert notice is not None
    assert "declare_complete" in notice


class _NoopHandler:
    def __call__(
        self, session: object, workspace: object, params: dict[str, object]
    ) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "ok"}]}


def _server_with_budget(budget: SessionWrapupBudget) -> McpServer:
    bridge = ToolBridge()
    bridge.register(
        ToolMetadata(
            definition=ToolDefinition(
                name="read_file",
                description="Test tool",
                input_schema={"type": "object"},
            ),
            required_capability="workspace.read",
        ),
        cast("Any", _NoopHandler()),
    )
    return McpServer(
        session=cast("Any", object()),
        workspace=cast("Any", object()),
        registry=bridge,
        wrapup_provider=budget.notice,
    )


def _call_read_file(server: McpServer) -> list[dict[str, object]]:
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        msg_id="1",
        params={"name": "read_file", "arguments": {}},
    )
    response, _ = server._handle_tools_call(request, ServerState.RUNNING)
    assert response.result is not None
    return cast("list[dict[str, object]]", response.result["content"])


def test_tool_result_carries_wrapup_banner_only_after_soft_threshold() -> None:
    clock = FakeClock()
    budget = SessionWrapupBudget(clock, soft_seconds=3000.0, hard_seconds=3300.0)
    server = _server_with_budget(budget)

    # Before the soft threshold: no banner.
    content_before = _call_read_file(server)
    assert all("time budget" not in str(block.get("text", "")) for block in content_before)

    clock.advance(3001.0)
    content_after = _call_read_file(server)
    assert any("declare_complete" in str(block.get("text", "")) for block in content_after)
