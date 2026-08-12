"""Regression coverage for deterministic harness tool-result echoes."""

from __future__ import annotations

from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy
from ralph.agents.timeout_clock import FakeClock


def test_watchdog_regression_harness_echo_tool_results_do_not_advance_meaningful_output() -> None:
    """S-2: repeated prompt echoes must not satisfy the LLM-output watchdog."""
    clock = FakeClock(start=100.0)
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            no_output_at_start_seconds=30.0,
            post_tool_result_progression_seconds=None,
            repeated_error_consecutive_threshold=None,
            repeated_error_window_count=None,
            repeated_error_window_seconds=None,
            activity_evidence_ttl_seconds=None,
        ),
        clock,
    )
    watchdog.record_invocation_start()
    invocation_start = watchdog._last_meaningful_output_at

    for _ in range(100):
        watchdog.record_tool_result_activity(is_harness_echo=True)
        clock.advance(0.01)

    assert watchdog._last_meaningful_output_at == invocation_start
    assert watchdog._has_meaningful_output is False
