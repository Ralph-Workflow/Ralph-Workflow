"""Regression coverage for the configured initial-output watchdog grace."""

from __future__ import annotations

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.invoke._options import (
    _policy_from_options,
    build_invoke_options_from_config,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.general_config import GeneralConfig


def test_claude_startup_regression_default_grace_tolerates_30_seconds_then_fires_at_120() -> None:
    """S-3: A silent cold start has a bounded 120-second grace period."""
    clock = FakeClock()
    watchdog = IdleWatchdog(TimeoutPolicy(idle_timeout_seconds=300.0), clock)
    watchdog.record_invocation_start()

    clock.advance(30.0)
    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) != WatchdogVerdict.FIRE

    clock.advance(90.0)
    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START


def test_claude_startup_regression_positive_override_reaches_timeout_policy() -> None:
    """S-3: The [general] override controls the runtime watchdog threshold."""
    config = GeneralConfig(agent_no_output_at_start_seconds=75.0)

    policy = _policy_from_options(build_invoke_options_from_config(config))

    assert policy.no_output_at_start_seconds == 75.0


def test_claude_startup_regression_rejects_non_positive_override() -> None:
    """S-3: Operators cannot disable bounded startup detection with zero."""
    try:
        GeneralConfig(agent_no_output_at_start_seconds=0.0)
    except ValueError as error:
        assert "agent_no_output_at_start_seconds" in str(error)
    else:
        raise AssertionError("zero startup grace must be rejected")
