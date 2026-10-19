"""Tests for rolling-window budget decision (R1)."""

from __future__ import annotations

from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy, next_budget_decision


class TestNextBudgetDecision:
    def test_rolling_cap_hit_when_7_timestamps_in_60s(self) -> None:
        """Rolling cap triggers when 7 events land within 60 s (rolling_max=6)."""
        policy = McpRestartPolicy(rolling_max_in_window=6)
        now = 100.0
        history = tuple(100.0 - i * 9.0 for i in range(7))  # 7 events within 60s
        result = next_budget_decision(
            policy,
            current_count=2,
            rolling_history=history,
            rolling_window_s=60.0,
            _now=lambda: now,
        )
        assert result == "window_drain"

    def test_absolute_cap_hit_when_current_count_equals_max_restarts(self) -> None:
        """Absolute cap triggers when current_count >= max_restarts."""
        policy = McpRestartPolicy(max_restarts=20, rolling_max_in_window=6)
        result = next_budget_decision(
            policy,
            current_count=20,
            rolling_history=(),
            rolling_window_s=60.0,
            _now=lambda: 0.0,
        )
        assert result == "absolute_cap"

    def test_next_budget_decision_absolute_cap_current_count_20_history_empty(self) -> None:
        """Returns absolute_cap when current_count=20 and history is empty."""
        policy = McpRestartPolicy(max_restarts=20)
        result = next_budget_decision(
            policy,
            current_count=20,
            rolling_history=(),
            rolling_window_s=60.0,
            _now=lambda: 0.0,
        )
        assert result == "absolute_cap"

    def test_next_budget_decision_window_drain_6_events_in_last_60s(self) -> None:
        """Returns window_drain when 6 events in last 60s but current_count=2."""
        policy = McpRestartPolicy(max_restarts=20, rolling_max_in_window=6)
        now = 100.0
        history = tuple(100.0 - i * 10.0 for i in range(6))  # 6 events in 60s
        result = next_budget_decision(
            policy,
            current_count=2,
            rolling_history=history,
            rolling_window_s=60.0,
            _now=lambda: now,
        )
        assert result == "window_drain"
