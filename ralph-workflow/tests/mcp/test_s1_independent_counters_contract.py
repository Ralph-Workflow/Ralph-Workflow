from __future__ import annotations

import pathlib
import tempfile

import pytest

from ralph.mcp.server import lifecycle as lifecycle_module
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.server.lifecycle import RestartAwareMcpBridge


class _FakeProcess:
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


def test_check_health_path_increments_restart_count_before_attempt() -> None:
    bridge = _make_bridge()

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.restart_count == 1

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.restart_count == 2

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.restart_count == 3

    with pytest.raises(McpServerError) as exc_budget:
        bridge.check_health_and_restart_if_needed()
    assert exc_budget.value.restart_count == 3
    msg = str(exc_budget.value)
    assert "restart budget" in msg
    assert "exhausted" in msg
    assert "restart budget exhausted" in msg
    assert "current=3" in msg


def test_reset_tool_registry_path_increments_tool_registry_resets_before_attempt() -> None:
    bridge = _make_bridge()

    assert bridge.restart_count == 0
    assert bridge.tool_registry_resets == 0

    max_resets = lifecycle_module._TOOL_REGISTRY_MAX_RESETS
    assert max_resets == 3

    for i in range(1, max_resets + 1):
        assert bridge.reset_tool_registry() is None
        assert bridge.restart_count == i, f"restart_count must be {i} — shared counter accounting"
        assert bridge.tool_registry_resets == i

    with pytest.raises(McpServerError) as exc_cap:
        bridge.reset_tool_registry()
    msg = str(exc_cap.value)
    assert "tool-registry-reset exhausted" in msg
    assert str(max_resets) in msg

    assert bridge.restart_count == max_resets
    assert bridge.tool_registry_resets == max_resets
