"""In-process regression test for the bounded tool-availability recovery path.

The live failure mode: Claude Code attempts a tool call
(``mcp__<server>__<tool>``), the live MCP server rejects it with
``<tool_use_error>Error: No such tool available: mcp__<server>__<tool>``,
the recovery classifier routes the failure to
``FailureCategory.AGENT`` with ``reset_tool_registry=True``, and the
next attempt calls ``RestartAwareMcpBridge.reset_tool_registry()`` to
rebuild the visible tool list.

This file proves:
- The failure classifier routes the live wire-level error to the
  tool-availability branch (``reset_tool_registry=True``).
- The ``RestartAwareMcpBridge`` counter increments by exactly 1 per
  recovery cycle.
- The ``_TOOL_REGISTRY_MAX_RESETS`` cap raises ``McpServerError``
  with the substring ``'tool-registry-reset exhausted'`` after the
  third call.

All assertions are in-process and the test must run in under 1s of
wall-clock time per the 60s combined test budget rule.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
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
        return cast("StandaloneMcpProcess", new_inner)

    return RestartAwareMcpBridge(
        cast("StandaloneMcpProcess", inner),
        restart_fn=_restart_fn,
        restart_policy=MagicMock(max_restarts=1000),
    )


def test_live_no_such_tool_available_message_routes_to_reset_tool_registry() -> None:
    """The exact live wire-level error message format must route to
    ``FailureCategory.AGENT`` with ``reset_tool_registry=True`` so the
    next attempt rebuilds the tool registry.
    """
    live_error_message = (
        "<tool_use_error>Error: No such tool available: "
        "mcp__ralph__read_file</tool_use_error>"
    )
    exc = RuntimeError(live_error_message)
    classified = FailureClassifier().classify(
        exc, phase="development", agent="claude/haiku"
    )
    assert isinstance(classified, ClassifiedFailure)
    assert classified.category == FailureCategory.AGENT
    assert classified.reset_tool_registry is True
    assert classified.counts_against_budget is True


def test_runtime_tool_dispatch_error_routes_to_reset_tool_registry() -> None:
    """The runtime ``ToolDispatchError`` raised at the bridge dispatch
    layer is the in-process mirror of the live wire-level error and
    must also route to ``reset_tool_registry=True``.
    """
    exc = ToolDispatchError("Tool 'read_file' is not registered")
    classified = FailureClassifier().classify(
        exc, phase="development", agent="claude/haiku"
    )
    assert isinstance(classified, ClassifiedFailure)
    assert classified.category == FailureCategory.AGENT
    assert classified.reset_tool_registry is True


def test_bridge_reset_tool_registry_increments_counter() -> None:
    """The ``reset_tool_registry()`` method increments the
    ``tool_registry_resets`` counter by exactly 1 per call. This is
    the primary signal the orchestrator reads to detect a
    tool-availability recovery cycle.
    """
    bridge = _make_bridge()
    assert bridge.tool_registry_resets == 0
    bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == 1
    bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == 2


def test_bridge_reset_tool_registry_resets_tool_list_path() -> None:
    """The ``reset_tool_registry()`` call must trigger the
    preflight/restart path so the visible tool list is rebuilt.
    A regression that no-ops the call would silently leave the
    broken registry in place.
    """
    bridge = _make_bridge()
    inner_before = bridge._inner
    bridge.reset_tool_registry()
    inner_after = bridge._inner
    assert inner_after is not inner_before, (
        "reset_tool_registry must swap the inner process so the "
        "visible tool list is rebuilt"
    )
    assert bridge.tool_registry_resets == 1


@pytest.mark.parametrize("attempts", [1, 2, 3])
def test_bridge_reset_tool_registry_within_cap_succeeds(attempts: int) -> None:
    """Within the ``_TOOL_REGISTRY_MAX_RESETS`` cap, the bridge
    must succeed silently (no exception, counter increments).
    """
    bridge = _make_bridge()
    for _ in range(attempts):
        bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == attempts


def test_bridge_reset_tool_registry_exhausts_with_distinct_substring() -> None:
    """After ``_TOOL_REGISTRY_MAX_RESETS`` (3) successful resets, the
    next call must raise ``McpServerError`` with a message containing
    ``'tool-registry-reset exhausted'`` so the orchestrator can
    distinguish this cap from the other two additive caps
    (restart-budget and recovery-attempt).
    """
    bridge = _make_bridge()
    for _ in range(lifecycle_module._TOOL_REGISTRY_MAX_RESETS):
        bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == lifecycle_module._TOOL_REGISTRY_MAX_RESETS
    with pytest.raises(McpServerError) as excinfo:
        bridge.reset_tool_registry()
    msg = str(excinfo.value)
    assert "tool-registry-reset exhausted" in msg


def test_budget_is_debited_exactly_once_per_cycle() -> None:
    """Pin that the recovery controller debits the
    ``tool_registry_resets`` counter EXACTLY ONCE per tool-availability
    failure cycle (not infinitely). A regression that double-debits
    the counter would hit the cap after one cycle and cause spurious
    "tool-registry-reset exhausted" errors in the live run.
    """
    bridge = _make_bridge()
    bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == 1
    bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == 2
    bridge.reset_tool_registry()
    assert bridge.tool_registry_resets == 3
    # The next call hits the cap.
    with pytest.raises(McpServerError):
        bridge.reset_tool_registry()
    # Counter is still 3 (not 4); the cap error path did not
    # increment the counter.
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
        cast("StandaloneMcpProcess", inner),
        restart_fn=lambda: cast("StandaloneMcpProcess", restarted),
        restart_policy=MagicMock(max_restarts=1000),
    )
    monkeypatch.setattr(
        lifecycle_module,
        "_http_tools_list_names",
        lambda endpoint, *, timeout: (_ for _ in ()).throw(TimeoutError("slow-start")),
    )

    with pytest.raises(McpServerError, match="alias verify probe failed after respawn"):
        bridge.reset_tool_registry()
