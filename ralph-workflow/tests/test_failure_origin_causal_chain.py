from __future__ import annotations

from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.agents.invoke._agent_invocation_error import AgentInvocationError
from ralph.process._agent_launch_error import AgentLaunchError


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
