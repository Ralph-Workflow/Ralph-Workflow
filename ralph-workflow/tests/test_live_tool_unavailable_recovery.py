from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ralph.mcp.server import lifecycle as lifecycle_module
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.server.lifecycle import RestartAwareMcpBridge
from ralph.mcp.tools.bridge._tool_dispatch_error import ToolDispatchError
from ralph.recovery.classified_failure import ClassifiedFailure
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import FailureClassifier


def _make_bridge() -> RestartAwareMcpBridge:
    inner = MagicMock(spec=StandaloneMcpProcess)
    inner.endpoint = "http://127.0.0.1:9999/mcp"
    counter = {"n": 0}

    def _restart_fn() -> StandaloneMcpProcess:
        counter["n"] += 1
        new_inner = MagicMock(spec=StandaloneMcpProcess)
        new_inner.endpoint = inner.endpoint
        return new_inner

    return RestartAwareMcpBridge(
        inner,
        restart_fn=_restart_fn,
        restart_policy=MagicMock(max_restarts=1000),
        run_id="test-run",
    )


def test_live_no_such_tool_available_message_routes_to_reset_tool_registry() -> None:
    live_error_message = (
        "<tool_use_error>Error: No such tool available: mcp__ralph__read_file</tool_use_error>"
    )
    exc = RuntimeError(live_error_message)
    classified = FailureClassifier().classify(exc, phase="development", agent="claude/haiku")
    assert isinstance(classified, ClassifiedFailure)
    assert classified.category == FailureCategory.AGENT
    assert classified.reset_tool_registry is True
    assert classified.counts_against_budget is True


def test_runtime_tool_dispatch_error_routes_to_reset_tool_registry() -> None:
    exc = ToolDispatchError("Tool 'read_file' is not registered")
    classified = FailureClassifier().classify(exc, phase="development", agent="claude/haiku")
    assert isinstance(classified, ClassifiedFailure)
    assert classified.category == FailureCategory.AGENT
    assert classified.reset_tool_registry is True


def test_bridge_reset_tool_registry_increments_counter() -> None:
    bridge = _make_bridge()
    assert bridge.tool_registry_resets == 0
    bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == 1
    bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == 2


def test_bridge_reset_tool_registry_resets_tool_list_path() -> None:
    bridge = _make_bridge()
    inner_before = bridge._inner
    bridge.reset_tool_registry()
    inner_after = bridge._inner
    assert inner_after is not inner_before, (
        "reset_tool_registry must swap the inner process so the visible tool list is rebuilt"
    )
    assert bridge.tool_registry_resets == 1


@pytest.mark.parametrize("attempts", [1, 2, 3])
def test_bridge_reset_tool_registry_within_cap_succeeds(attempts: int) -> None:
    bridge = _make_bridge()
    for _ in range(attempts):
        bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == attempts


def test_bridge_reset_tool_registry_exhausts_with_distinct_substring() -> None:
    bridge = _make_bridge()
    for _ in range(lifecycle_module._TOOL_REGISTRY_MAX_RESETS):
        bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == lifecycle_module._TOOL_REGISTRY_MAX_RESETS
    with pytest.raises(McpServerError) as excinfo:
        bridge.reset_tool_registry()
    msg = str(excinfo.value)
    assert "tool-registry-reset exhausted" in msg


def test_budget_is_debited_exactly_once_per_cycle() -> None:
    bridge = _make_bridge()
    bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == 1
    bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == 2
    bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == 3
    with pytest.raises(McpServerError):
        bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == lifecycle_module._TOOL_REGISTRY_MAX_RESETS


def test_bridge_reset_tool_registry_fails_closed_when_alias_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = SimpleNamespace(
        endpoint="http://127.0.0.1:9999/mcp",
        process=SimpleNamespace(poll=lambda: None),
        shutdown=lambda: None,
    )
    restarted = SimpleNamespace(
        endpoint="http://127.0.0.1:9999/mcp",
        process=SimpleNamespace(poll=lambda: None),
        shutdown=lambda: None,
    )
    bridge = RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: restarted,
        restart_policy=MagicMock(max_restarts=1000),
        run_id="test-run",
    )
    monkeypatch.setattr(
        lifecycle_module,
        "_http_tools_list_names",
        lambda endpoint, *, timeout: (_ for _ in ()).throw(TimeoutError("slow-start")),
    )

    with pytest.raises(McpServerError, match="alias verify probe failed after respawn"):
        bridge.reset_tool_registry()
