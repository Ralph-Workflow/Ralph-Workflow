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
from ralph.timeout_defaults import NO_OUTPUT_AT_START_SECONDS


def test_startup_regression_default_grace_is_bounded_and_fires_after_it_elapses() -> None:
    """S-3: A silent cold start has a bounded grace period, then fires.

    This used to assert the grace was 15 seconds, on the reasoning that a
    silent startup is "unambiguously a broken agent". That reasoning was
    backwards: an agent owes no output before its first tool call, and
    OpenCode's JSON stream emits nothing until then -- measured at 14.8s,
    23.2s and 20.2s to first frame -- so a 15s ceiling killed it before it
    could speak, and the operator saw no output at all. The duration is read
    from the production constant rather than written as a literal, because it
    has moved (120 -> 30 -> 15 -> 360) and a hardcoded advance silently stops
    exercising the fire path each time. The BOUND is the contract; the value
    is pinned against the measurement in
    ``tests/agents/test_startup_grace_accommodates_silent_agents.py``.
    """
    clock = FakeClock()
    watchdog = IdleWatchdog(TimeoutPolicy(idle_timeout_seconds=300.0), clock)
    watchdog.record_invocation_start()

    clock.advance(NO_OUTPUT_AT_START_SECONDS - 1.0)
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
