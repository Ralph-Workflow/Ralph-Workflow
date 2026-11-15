from __future__ import annotations

from pathlib import Path

import pytest

from ralph.mcp.explore.recovery import RecoveryScheduler, run_recovery_action
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy, next_budget_decision
from ralph.pipeline import attempt_runner


def test_wu_c2_mcp_restart_policy_is_process_budget_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launches: list[object] = []

    def fail_if_agent_launched(*args: object, **kwargs: object) -> object:
        launches.extend(args)
        launches.extend(kwargs.values())
        raise AssertionError("MCP restart policy must not launch an agent")

    monkeypatch.setattr(attempt_runner, "run_agent_attempt", fail_if_agent_launched)
    policy = McpRestartPolicy(max_restarts=2, rolling_max_in_window=1)

    assert next_budget_decision(policy, 0, (), 60.0, _now=lambda: 10.0) == "ok"
    assert next_budget_decision(policy, 2, (), 60.0, _now=lambda: 10.0) == "absolute_cap"
    assert next_budget_decision(policy, 0, (9.0,), 60.0, _now=lambda: 10.0) == "window_drain"
    assert launches == []


def test_wu_c2_explore_recovery_is_local_index_recovery_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launches: list[object] = []

    def fail_if_agent_launched(*args: object, **kwargs: object) -> object:
        launches.extend(args)
        launches.extend(kwargs.values())
        raise AssertionError("explore recovery must not launch an agent")

    monkeypatch.setattr(attempt_runner, "run_agent_attempt", fail_if_agent_launched)
    scheduler = RecoveryScheduler(workspace_root=Path("/workspace"), _load_persisted=False)

    result = run_recovery_action(
        scheduler,
        workspace_root=Path("/workspace"),
        fault_code="",
    )

    assert result["health"] == "healthy"
    assert result["recovery_attempts"] == 0
    assert launches == []
