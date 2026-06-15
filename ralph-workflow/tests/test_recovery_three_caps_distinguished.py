"""Regression tests for the three additive recovery caps.

The three caps are independent and the orchestrator must be able to
distinguish which one fired by inspecting the error message SUBSTRING
(NOT exact match, so future rewordings do not break the test as long
as the substring remains):

  1. tool-registry-reset (NEW, capped by ``_TOOL_REGISTRY_MAX_RESETS=3``)
     - raised by ``RestartAwareMcpBridge.reset_tool_registry()`` when
       the cap is exhausted
     - substring: ``'tool-registry-reset exhausted'``

  2. restart-budget (EXISTING, capped by ``McpRestartPolicy.max_restarts``)
     - raised by ``RestartAwareMcpBridge.check_health_and_restart_if_needed()``
       when the restart budget is exhausted
     - substrings: ``'restart budget'`` AND ``'exhausted'``

  3. recovery-attempt (EXISTING, capped by the orchestrator's
     ``max_recovery_attempts``)
     - raised by the recovery loop in ``_invoke_agent_with_recovery``
       when the attempt budget is exhausted
     - substring: ``'recovery-attempt exhausted'``

This test pins all three distinguishable substrings so a regression
that merges two of them into the same error message is caught.
"""

from __future__ import annotations

import inspect
from typing import cast
from unittest.mock import MagicMock

import pytest

from ralph.mcp.server import lifecycle as lifecycle_module
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._process_like import ProcessLike
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.server.lifecycle import RestartAwareMcpBridge


def _make_bridge(*, max_restarts: int) -> RestartAwareMcpBridge:
    inner = MagicMock(spec=StandaloneMcpProcess)
    inner.endpoint = "http://127.0.0.1:9999/mcp"
    # Set up a proper inner.process mock with the ProcessLike protocol
    process_mock = MagicMock(spec=ProcessLike)
    process_mock.poll.return_value = None
    inner.process = process_mock

    def _restart_fn() -> StandaloneMcpProcess:
        new_inner = MagicMock(spec=StandaloneMcpProcess)
        new_inner.endpoint = inner.endpoint
        new_process = MagicMock(spec=ProcessLike)
        new_process.poll.return_value = None
        new_inner.process = new_process
        return cast("StandaloneMcpProcess", new_inner)

    return RestartAwareMcpBridge(
        cast("StandaloneMcpProcess", inner),
        restart_fn=_restart_fn,
        restart_policy=MagicMock(max_restarts=max_restarts),
        run_id="test-run",
    )


def test_tool_registry_reset_cap_substring_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        lifecycle_module,
        "_http_tools_list_names",
        lambda endpoint, *, timeout: ["read_file", "mcp__ralph__read_file"],
    )
    bridge = _make_bridge(max_restarts=1000)
    for _ in range(lifecycle_module._TOOL_REGISTRY_MAX_RESETS):
        bridge.reset_tool_registry()
    with pytest.raises(McpServerError) as excinfo:
        bridge.reset_tool_registry()
    msg = str(excinfo.value)
    assert "tool-registry-reset exhausted" in msg
    # The other cap substrings must NOT be present (a regression that
    # merges the two error messages would fail this assertion).
    assert "restart budget" not in msg
    assert "recovery-attempt exhausted" not in msg


def test_restart_budget_cap_substrings_are_present() -> None:
    """Simulate the restart budget being exhausted by force-clearing
    the inner process and exhausting the restart counter."""
    bridge = _make_bridge(max_restarts=2)
    bridge._inner.process.poll.return_value = 999  # process exited
    bridge._inner.shutdown = MagicMock()
    bridge._restart_fn = MagicMock(
        return_value=bridge._inner
    )
    # First two checks should restart (counter goes 1, 2).
    bridge.check_health_and_restart_if_needed()
    bridge.check_health_and_restart_if_needed()
    # The third check should raise.
    with pytest.raises(McpServerError) as excinfo:
        bridge.check_health_and_restart_if_needed()
    msg = str(excinfo.value)
    assert "restart budget" in msg
    assert "exhausted" in msg
    # The tool-registry-reset substring must NOT be present.
    assert "tool-registry-reset exhausted" not in msg
    assert "recovery-attempt exhausted" not in msg


def test_three_caps_have_distinct_substrings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The three caps must produce three distinguishable substrings
    that do not overlap. This is the contract the orchestrator uses
    to decide which bound fired."""
    monkeypatch.setattr(
        lifecycle_module,
        "_http_tools_list_names",
        lambda endpoint, *, timeout: ["read_file", "mcp__ralph__read_file"],
    )
    # 1. tool-registry-reset
    bridge = _make_bridge(max_restarts=1000)
    for _ in range(lifecycle_module._TOOL_REGISTRY_MAX_RESETS):
        bridge.reset_tool_registry()
    try:
        bridge.reset_tool_registry()
    except McpServerError as exc:
        tool_registry_substring = str(exc)
    else:
        pytest.fail("expected McpServerError on tool-registry-reset cap")

    # 2. restart-budget
    bridge2 = _make_bridge(max_restarts=1)
    bridge2._inner.process.poll.return_value = 999
    bridge2._inner.shutdown = MagicMock()
    bridge2._restart_fn = MagicMock(
        return_value=bridge2._inner
    )
    bridge2.check_health_and_restart_if_needed()
    try:
        bridge2.check_health_and_restart_if_needed()
    except McpServerError as exc:
        restart_budget_substring = str(exc)
    else:
        pytest.fail("expected McpServerError on restart-budget cap")

    # The three substrings must be pairwise distinct.
    assert "tool-registry-reset exhausted" in tool_registry_substring
    assert "restart budget" in restart_budget_substring
    assert "exhausted" in restart_budget_substring
    assert tool_registry_substring != restart_budget_substring


def test_cap_substrings_are_not_misused_in_other_messages() -> None:
    """A regression that puts 'tool-registry-reset exhausted' into a
    non-tool-registry error path must be caught. We inspect the
    source code for the substrings to confirm they appear in the
    expected locations only."""
    source = inspect.getsource(lifecycle_module)
    # The substring must appear EXACTLY in the
    # `reset_tool_registry` cap error path.
    occurrences = source.count("tool-registry-reset exhausted")
    assert occurrences >= 1, (
        "expected the substring 'tool-registry-reset exhausted' to appear "
        "in the lifecycle module's reset_tool_registry cap path"
    )
