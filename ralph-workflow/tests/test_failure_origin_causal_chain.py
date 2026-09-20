from __future__ import annotations

import pytest

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._agent_invocation_error import AgentInvocationError
from ralph.agents.invoke._direct_mcp_recovery import run_with_direct_mcp_recovery
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.process._agent_launch_error import AgentLaunchError
from ralph.process._spawn_validation import prepare_spawn_command
from ralph.runtime_events import current_runtime_event


def test_runtime_launch_origin_is_not_lost_when_wrapped() -> None:
    cause = OSError(7, "Argument list too long")
    exc = AgentLaunchError("claude", cause, 123)
    wrapper = RuntimeError("recovery")
    wrapper.__cause__ = exc

    assert exc.failure_origin == "runtime_launch"
    assert wrapper.__cause__ is exc


def test_watchdog_origin_and_issuer_are_structural() -> None:
    exc = IdleWatchdogKilledError("no_progress_quiet", 15, issuer="watchdog-evaluator")

    assert exc.failure_origin == "watchdog_observation"
    assert exc.issuer == "watchdog-evaluator"


def test_plain_agent_failure_stays_agent_owned() -> None:
    exc = AgentInvocationError("claude", 1)

    assert exc.failure_origin == "agent"
    assert exc.issuer is None


def test_direct_mcp_recovery_records_reset_for_following_watchdog() -> None:
    """S-3: real reset producer reaches the next attempt's watchdog consumer."""
    calls = 0
    observed: list[IdleWatchdogKilledError] = []

    def attempt(_session_id: str | None, _capture: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            _capture("sess")
            raise AgentInvocationError("claude", 1, "Model returned an empty response; No such tool available: mcp__ralph__read_file", parsed_output=['{"type":"tool_result"}'])
        exc = IdleWatchdogKilledError(
            "no_output_at_start", 15, runtime_event=current_runtime_event()
        )
        observed.append(exc)
        raise exc

    with pytest.raises(IdleWatchdogKilledError):
        run_with_direct_mcp_recovery(
            attempt, max_retries=1, reset_tool_registry=lambda: None
        )

    assert observed[0].runtime_event == "mcp_operation"


def test_inactivity_error_retains_watchdog_runtime_event() -> None:
    watchdog = IdleWatchdogKilledError("no_output_at_start", 15, runtime_event="mcp_operation")
    exc = AgentInactivityTimeoutError(
        "claude",
        1.0,
        opts=InactivityTimeoutOpts(
            reason=WatchdogFireReason.NO_OUTPUT_AT_START,
            runtime_event=watchdog.runtime_event,
        ),
    )
    exc.__cause__ = watchdog

    assert exc.failure_origin == "watchdog_observation"
    assert exc.runtime_event == "mcp_operation"
    assert exc.__cause__.runtime_event == "mcp_operation"


def test_direct_mcp_recovery_regression_does_not_leak_runtime_events() -> None:
    """S-3: a fresh recovery loop starts with no prior invocation event."""
    observed: list[object] = []

    def attempt(_session_id: str | None, _capture: object) -> str:
        observed.append(current_runtime_event())
        return "ok"

    assert run_with_direct_mcp_recovery(attempt, max_retries=0) == "ok"
    assert observed == [None]


def test_oversized_spawn_payload_records_runtime_launch_event() -> None:
    """S-3: validation-side E2BIG is a real runtime-launch producer."""
    from ralph.runtime_events import RuntimeEventRecorder, runtime_event_scope

    recorder = RuntimeEventRecorder()
    with runtime_event_scope(recorder), pytest.raises(AgentLaunchError):
        prepare_spawn_command(
            ("claude", "--", "x" * 100),
            cwd=None,
            env={"A": "y" * 100},
            payload_limit=32,
        )

    assert recorder.latest() == "runtime_launch"

