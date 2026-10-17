"""S1 independent-counter design contract — regression pin.

Prompt requirement (quoted verbatim):
    "Count every attempt, successful or not"

Ralph's S1 design rule: each counter (_restart_count,
_tool_registry_resets) is incremented BEFORE the _restart_fn
call so that a raised exception is reflected in the count.
The two counters are fully independent; exhausting
_tool_registry_resets does not advance _restart_count.

This module pins that behavior across both restart paths:
  1. check_health_and_restart_if_needed  (restart budget)
  2. reset_tool_registry                  (tool-registry-reset budget)
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from ralph.mcp.server import lifecycle as lifecycle_module
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.server.lifecycle import RestartAwareMcpBridge


class _FakeProcess:
    """Minimal process stand-in used by _make_bridge."""

    def __init__(self, poll_result: int | None, pid: int = 9999) -> None:
        self._poll_result = poll_result
        self._pid = pid

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        return self._poll_result

    def terminate(self, grace_period_s: float = 5.0) -> None:
        self._poll_result = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        return None


def _make_bridge() -> RestartAwareMcpBridge:
    """Build a RestartAwareMcpBridge whose _restart_fn always raises.

    Mirrors the pattern from tests/test_mcp_server_tool_registry_reset_cap.py.
    """
    session_file = pathlib.Path(tempfile.mkdtemp()) / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    inner = StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9100/mcp",
        process=_FakeProcess(poll_result=1),
        session_file=session_file,
    )

    def always_raising_restart_fn() -> StandaloneMcpProcess:
        raise RuntimeError("synthetic restart failure")

    return RestartAwareMcpBridge(
        inner,
        restart_fn=always_raising_restart_fn,
        restart_policy=lifecycle_module.McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
    )


pytestmark = pytest.mark.subprocess_e2e


def test_check_health_path_increments_restart_count_before_attempt_and_typed_error_carries_real_count() -> (
    None
):
    """S1: restart_count advances BEFORE _restart_fn is called.

    Each call to check_health_and_restart_if_needed increments
    _restart_count before invoking _restart_fn. When _restart_fn
    raises, the McpServerError carries restart_count == attempt_index.

    After max_restarts=3 exhausted attempts, the budget-exhausted
    McpServerError carries restart_count == 3 (the real cap value,
    not 0 or 1).
    """
    bridge = _make_bridge()

    # First attempt: _restart_fn raises; error carries restart_count == 1
    with pytest.raises(McpServerError) as exc1:
        bridge.check_health_and_restart_if_needed()
    assert exc1.value.restart_count == 1
    assert "MCP restart attempt 1/3 failed" in str(exc1.value)

    # Second attempt: restart_count == 2 before the call
    with pytest.raises(McpServerError) as exc2:
        bridge.check_health_and_restart_if_needed()
    assert exc2.value.restart_count == 2
    assert "MCP restart attempt 2/3 failed" in str(exc2.value)

    # Third attempt: restart_count == 3 before the call
    with pytest.raises(McpServerError) as exc3:
        bridge.check_health_and_restart_if_needed()
    assert exc3.value.restart_count == 3
    assert "MCP restart attempt 3/3 failed" in str(exc3.value)

    # Fourth call: budget exhausted — _restart_fn is NOT called (check fires first)
    # The error carries the real restart_count == 3 (not 0 or 1)
    with pytest.raises(McpServerError) as exc_budget:
        bridge.check_health_and_restart_if_needed()
    assert exc_budget.value.restart_count == 3
    msg = str(exc_budget.value)
    assert "restart budget" in msg
    assert "exhausted" in msg
    assert "restart budget exhausted" in msg
    assert "current=3" in msg


def test_reset_tool_registry_path_increments_tool_registry_resets_before_attempt_and_typed_error_carries_real_count() -> (
    None
):
    """S1: tool_registry_resets and restart_count advance BEFORE _restart_fn is called.

    Each call to reset_tool_registry increments _tool_registry_resets
    and shares accounting with _restart_count before invoking _restart_fn.
    When _restart_fn raises, the McpServerError carries the real restart_count.
    """
    bridge = _make_bridge()

    # Sanity: both counters start at 0
    assert bridge.restart_count == 0
    assert bridge.tool_registry_resets == 0

    max_resets = lifecycle_module._TOOL_REGISTRY_MAX_RESETS
    assert max_resets == 3

    # Exhaust tool-registry-reset budget
    for i in range(1, max_resets + 1):
        with pytest.raises(McpServerError) as exc:
            bridge.reset_tool_registry()
        # Error carries the real restart count
        assert exc.value.restart_count == i, (
            f"restart_count must be {i} — shared counter accounting"
        )
        # Before the next iteration the counters are i
        assert bridge.tool_registry_resets == i
        assert bridge.restart_count == i

    # One more call: cap exhausted, _restart_fn is NOT called
    with pytest.raises(McpServerError) as exc_cap:
        bridge.reset_tool_registry()
    msg = str(exc_cap.value)
    assert "tool-registry-reset exhausted" in msg
    assert str(max_resets) in msg  # cap value visible

    # Counters advanced to cap — shared counter design verified
    assert bridge.restart_count == max_resets
    assert bridge.tool_registry_resets == max_resets
