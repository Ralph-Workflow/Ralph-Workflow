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
from ralph.timeout_defaults import NO_OUTPUT_AT_START_SECONDS


def test_startup_regression_default_grace_is_bounded_and_fires_after_it_elapses() -> None:
    clock = FakeClock()
    watchdog = IdleWatchdog(TimeoutPolicy(idle_timeout_seconds=300.0), clock)
    watchdog.record_invocation_start()

    clock.advance(NO_OUTPUT_AT_START_SECONDS - 1.0)
    assert (
        watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) != WatchdogVerdict.FIRE
    )

    clock.advance(2.0)
    assert (
        watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE) == WatchdogVerdict.FIRE
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START


def test_claude_startup_regression_positive_override_reaches_timeout_policy() -> None:
    config = GeneralConfig(agent_no_output_at_start_seconds=75.0)

    policy = _policy_from_options(build_invoke_options_from_config(config))

    assert policy.no_output_at_start_seconds == 75.0


@pytest.mark.parametrize("invalid_grace", [0.0, -1.0])
def test_claude_startup_regression_rejects_non_positive_override(
    invalid_grace: float,
) -> None:
    with pytest.raises(ValueError, match="agent_no_output_at_start_seconds"):
        GeneralConfig(agent_no_output_at_start_seconds=invalid_grace)
