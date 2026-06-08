"""The per-invocation wall-clock ceiling must be active by default.

The 5-hour runaway was possible because ``agent_max_session_seconds`` defaulted
to None (no ceiling). These tests pin the graduated defaults: a hard force-cut at
55 minutes and a soft wrap-up nag at 50 minutes, with the soft threshold strictly
below the hard ceiling.
"""

from __future__ import annotations

import pytest

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.general_config import GeneralConfig


def test_session_ceiling_default_is_active_and_graduated() -> None:
    config = GeneralConfig()
    assert config.agent_max_session_seconds == 3300.0
    assert config.agent_session_soft_wrapup_seconds == 3000.0
    assert config.agent_session_soft_wrapup_seconds < config.agent_max_session_seconds


def test_repeated_error_thresholds_default_active() -> None:
    config = GeneralConfig()
    assert config.agent_repeated_error_consecutive_threshold == 5
    assert config.agent_repeated_error_window_count == 8
    assert config.agent_repeated_error_window_seconds == 600.0


def test_watchdog_fires_session_ceiling_at_hard_cut() -> None:
    clock = FakeClock()
    policy = TimeoutPolicy(idle_timeout_seconds=300.0, max_session_seconds=3300.0)
    watchdog = IdleWatchdog(policy, clock)
    clock.advance(3299.0)
    assert (
        watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.RESUMABLE_CONTINUE)
        == WatchdogVerdict.CONTINUE
    )
    clock.advance(2.0)
    assert (
        watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.RESUMABLE_CONTINUE)
        == WatchdogVerdict.FIRE
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


def test_soft_wrapup_must_be_below_hard_ceiling() -> None:
    with pytest.raises(ValueError, match="agent_session_soft_wrapup_seconds must be <"):
        GeneralConfig(
            agent_session_soft_wrapup_seconds=4000.0,
            agent_max_session_seconds=3300.0,
        )
