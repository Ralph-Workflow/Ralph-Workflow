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

    def test_rolling_cap_trips_before_absolute_cap(self) -> None:
        """Rolling window cap trips even when total restarts are well below absolute cap."""
        policy = McpRestartPolicy(
            max_restarts=20,
            rolling_max_in_window=6,
            rolling_window_s=600.0,
            healthy_reset_s=300.0,
        )
        now = 500.0
        # 6 restarts clustered in the last 60 seconds (now - ts <= 600.0)
        history = tuple(500.0 - i * 10.0 for i in range(6))
        # current_count = 6 is well below max_restarts = 20
        result = next_budget_decision(
            policy,
            current_count=6,
            rolling_history=history,
            rolling_window_s=policy.rolling_window_s,
            _now=lambda: now,
        )
        assert result == "window_drain"

    def test_healthy_reset_clears_window(self) -> None:
        """When the server runs stably past the window, old failures age out and decision is ok."""
        policy = McpRestartPolicy(
            max_restarts=20,
            rolling_max_in_window=6,
            rolling_window_s=600.0,
            healthy_reset_s=300.0,
        )
        # 6 events at t = 100..150
        history = tuple(150.0 - i * 10.0 for i in range(6))

        # At t = 200, within the 600s rolling window, decision is window_drain
        result_during_window = next_budget_decision(
            policy,
            current_count=6,
            rolling_history=history,
            rolling_window_s=policy.rolling_window_s,
            _now=lambda: 200.0,
        )
        assert result_during_window == "window_drain"

        # At t = 800 (650s after the last failure, exceeding rolling_window_s and healthy_reset_s),
        # the window has cleared; decision returns "ok"
        result_after_reset = next_budget_decision(
            policy,
            current_count=6,
            rolling_history=history,
            rolling_window_s=policy.rolling_window_s,
            _now=lambda: 800.0,
        )
        assert result_after_reset == "ok"

    def test_policy_defaults_and_fields(self) -> None:
        """McpRestartPolicy defaults have rolling_window_s=600.0 and healthy_reset_s=300.0."""
        policy = McpRestartPolicy()
        assert policy.rolling_window_s == 600.0
        assert policy.healthy_reset_s == 300.0
        assert policy.max_restarts == 20
        assert policy.rolling_max_in_window == 6
        assert policy.cooldown_seconds == 2.0
