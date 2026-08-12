"""Regression coverage for the configured initial-output watchdog grace."""

from __future__ import annotations

import pytest

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


def test_claude_startup_regression_default_grace_tolerates_15_seconds_then_fires_at_15() -> None:
    """S-3: A silent cold start has a bounded 15-second grace period.

    The default startup ceiling sits just above the broken-agent grace
    window (12s): a silent startup is unambiguously a broken agent, so
    the watchdog's NO_OUTPUT_AT_START backstop fires at 15s rather than
    the historical 120s.
    """
    clock = FakeClock()
    watchdog = IdleWatchdog(TimeoutPolicy(idle_timeout_seconds=300.0), clock)
    watchdog.record_invocation_start()

    clock.advance(14.0)
    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) != WatchdogVerdict.FIRE

    clock.advance(2.0)
    assert watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START


def test_claude_startup_regression_positive_override_reaches_timeout_policy() -> None:
    """S-3: The [general] override controls the runtime watchdog threshold."""
    config = GeneralConfig(agent_no_output_at_start_seconds=75.0)

    policy = _policy_from_options(build_invoke_options_from_config(config))

    assert policy.no_output_at_start_seconds == 75.0


@pytest.mark.parametrize("invalid_grace", [0.0, -1.0])
def test_claude_startup_regression_rejects_non_positive_override(
    invalid_grace: float,
) -> None:
    """S-2: Operators cannot disable bounded startup detection with zero or negatives."""
    with pytest.raises(ValueError, match="agent_no_output_at_start_seconds"):
        GeneralConfig(agent_no_output_at_start_seconds=invalid_grace)
