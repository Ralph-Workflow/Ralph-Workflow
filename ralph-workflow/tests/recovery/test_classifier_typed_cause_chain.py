from __future__ import annotations

import errno

from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import FailureClassifier


class _IdleStreamTimeoutError(Exception):
    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        if cause is not None:
            self.__cause__ = cause


class _AgentInactivityTimeoutError(Exception):
    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        if cause is not None:
            self.__cause__ = cause


def test_classifier_reaches_typed_cause_one_hop_deep() -> None:
    typed = IdleWatchdogKilledError(reason="idle", signal=15)
    wrapper = _IdleStreamTimeoutError("watchdog fired", cause=typed)
    classified = FailureClassifier().classify(
        wrapper, phase="p", agent="a", connectivity_state="online"
    )
    assert classified.category == FailureCategory.AGENT, (
        f"typed cause one hop deep must classify as AGENT, got {classified.category}"
    )


def test_classifier_reaches_typed_cause_two_hops_deep() -> None:
    typed = IdleWatchdogKilledError(reason="no_output", signal=15)
    stream_timeout = _IdleStreamTimeoutError("watchdog fired", cause=typed)
    agent_inactivity = _AgentInactivityTimeoutError("agent idle for too long", cause=stream_timeout)
    classified = FailureClassifier().classify(
        agent_inactivity, phase="p", agent="a", connectivity_state="online"
    )
    assert classified.category == FailureCategory.AGENT, (
        f"typed cause two hops deep must classify as AGENT, got {classified.category}"
    )


def test_classifier_reaches_typed_cause_via_context_chain() -> None:
    typed = IdleWatchdogKilledError(reason="stalled", signal=15)
    wrapper = _IdleStreamTimeoutError("watchdog fired")
    wrapper.__context__ = typed
    classified = FailureClassifier().classify(wrapper, phase="p", agent="a")
    assert classified.category == FailureCategory.AGENT, (
        f"typed cause via __context__ must classify as AGENT, got {classified.category}"
    )


def test_classifier_no_typed_cause_falls_through_to_text() -> None:
    exc = OSError(errno.ETIMEDOUT, "timed out")
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.category == FailureCategory.ENVIRONMENTAL, (
        f"OSError(ETIMEDOUT) without typed cause must classify as ENVIRONMENTAL, "
        f"got {classified.category}"
    )


def test_classifier_typed_cause_cycle_does_not_hang() -> None:
    typed = IdleWatchdogKilledError(reason="idle", signal=15)
    wrapper = _IdleStreamTimeoutError("watchdog fired", cause=typed)
    typed.__cause__ = wrapper
    try:
        classified = FailureClassifier().classify(wrapper, phase="p", agent="a")
        assert classified.category == FailureCategory.AGENT
    finally:
        typed.__cause__ = None
