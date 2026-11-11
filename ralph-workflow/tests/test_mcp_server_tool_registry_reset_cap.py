from __future__ import annotations

import contextlib
from unittest.mock import MagicMock

import pytest

from ralph.mcp.server import lifecycle as lifecycle_module
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.server.lifecycle import RestartAwareMcpBridge


def _make_bridge() -> RestartAwareMcpBridge:
    inner = MagicMock(spec=StandaloneMcpProcess)
    inner.endpoint = "http://127.0.0.1:9999/mcp"
    counter = {"n": 0}

    def _restart_fn() -> StandaloneMcpProcess:
        counter["n"] += 1
        new_inner = MagicMock(spec=StandaloneMcpProcess)
        new_inner.endpoint = inner.endpoint
        return new_inner

    bridge = RestartAwareMcpBridge(
        inner,
        restart_fn=_restart_fn,
        restart_policy=MagicMock(max_restarts=1000),
        run_id="test-run",
    )
    return bridge


def test_tool_registry_resets_starts_at_zero() -> None:
    bridge = _make_bridge()
    assert bridge.tool_registry_resets == 0


def test_tool_registry_resets_increments_on_reset() -> None:
    bridge = _make_bridge()
    bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == 1
    bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == 2


def test_tool_registry_max_resets_constant_is_positive() -> None:
    assert lifecycle_module._TOOL_REGISTRY_MAX_RESETS > 0
    assert lifecycle_module._TOOL_REGISTRY_MAX_RESETS == 3


def test_reset_cap_raises_with_distinct_substring() -> None:
    bridge = _make_bridge()
    for _ in range(lifecycle_module._TOOL_REGISTRY_MAX_RESETS):
        bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == lifecycle_module._TOOL_REGISTRY_MAX_RESETS
    with pytest.raises(McpServerError) as excinfo:
        bridge.reset_tool_registry()
    msg = str(excinfo.value)
    assert "tool-registry-reset exhausted" in msg
    assert str(lifecycle_module._TOOL_REGISTRY_MAX_RESETS) in msg
    assert str(bridge.tool_registry_resets) in msg


def test_tool_registry_reset_increments_shared_restart_count() -> None:
    bridge = _make_bridge()
    bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == 1
    assert bridge.restart_count == 1


def test_tool_registry_reset_preserves_endpoint_uri() -> None:
    bridge = _make_bridge()
    bridge.reset_tool_registry()
    assert bridge.endpoint_uri() == "http://127.0.0.1:9999/mcp"
    assert bridge.agent_endpoint_uri() == "http://127.0.0.1:9999/mcp"


@pytest.mark.parametrize(
    "call_count",
    list(range(0, 4)),
    ids=["before_any_call", "after_one_call", "after_two_calls", "after_three_calls"],
)
def test_tool_registry_resets_counter_increments_monotonically(
    call_count: int,
) -> None:
    bridge = _make_bridge()
    for _ in range(call_count):
        with contextlib.suppress(McpServerError):
            bridge.reset_tool_registry()
    expected = min(call_count, lifecycle_module._TOOL_REGISTRY_MAX_RESETS)
    assert bridge.tool_registry_resets == expected
