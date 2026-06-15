"""Regression tests for the tool-registry-reset cap on RestartAwareMcpBridge.

The new ``_TOOL_REGISTRY_MAX_RESETS`` cap is additive with the existing
``McpRestartPolicy.max_restarts`` and the recovery controller's
``max_recovery_attempts``. When a tool-availability failure recurs
after the bridge has rebuilt its visible tool list, the orchestrator
needs a distinguishable cap error so the operator can diagnose which
bound fired.

The error message MUST contain the substring
``'tool-registry-reset exhausted'`` so the orchestrator can branch on
it independently of the other two cap substrings
(``'restart budget' + 'exhausted'`` and ``'recovery-attempt exhausted'``).
"""

from __future__ import annotations

import contextlib
from typing import cast
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
        return cast("StandaloneMcpProcess", new_inner)

    bridge = RestartAwareMcpBridge(
        cast("StandaloneMcpProcess", inner),
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
    """The cap is enforced at import time via `if/raise RuntimeError`."""
    assert lifecycle_module._TOOL_REGISTRY_MAX_RESETS > 0
    assert lifecycle_module._TOOL_REGISTRY_MAX_RESETS == 3


def test_reset_cap_raises_with_distinct_substring() -> None:
    """After `lifecycle_module._TOOL_REGISTRY_MAX_RESETS` (3) successful resets, the next
    call must raise McpServerError with a message containing
    'tool-registry-reset exhausted'."""
    bridge = _make_bridge()
    for _ in range(lifecycle_module._TOOL_REGISTRY_MAX_RESETS):
        bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == lifecycle_module._TOOL_REGISTRY_MAX_RESETS
    with pytest.raises(McpServerError) as excinfo:
        bridge.reset_tool_registry()
    msg = str(excinfo.value)
    assert "tool-registry-reset exhausted" in msg
    # The cap count and the current count should both be visible.
    assert str(lifecycle_module._TOOL_REGISTRY_MAX_RESETS) in msg
    assert str(bridge.tool_registry_resets) in msg


def test_tool_registry_reset_does_not_increment_restart_count() -> None:
    """The two counters are independent. A tool-registry reset must
    not be confused with a crash restart for diagnostic purposes."""
    bridge = _make_bridge()
    bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == 1
    assert bridge.restart_count == 0


def test_tool_registry_reset_preserves_endpoint_uri() -> None:
    """A tool-registry reset must reuse the same endpoint URI so the
    agent's MCP_ENDPOINT_ENV stays valid."""
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
