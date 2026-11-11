from __future__ import annotations

import errno

import pytest

from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import FailureClassifier


def test_idle_watchdog_kill_with_timeout_token_still_classified_as_agent() -> None:
    exc = IdleWatchdogKilledError(reason="idle", signal=15)
    classified = FailureClassifier().classify(exc, phase="prop-m", agent="prop-m-agent")
    assert classified.category == FailureCategory.AGENT, (
        f"IdleWatchdogKilledError must classify as AGENT, got {classified.category}"
    )


def test_idle_watchdog_kill_reason_is_preserved_in_classification() -> None:
    exc = IdleWatchdogKilledError(reason="stalled", signal=15)
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.category == FailureCategory.AGENT
    assert "stalled" in str(exc).lower() or "stalled" in classified.reason.lower()


def test_idle_watchdog_kill_counts_against_budget() -> None:
    exc = IdleWatchdogKilledError(reason="no_output", signal=15)
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.counts_against_budget is True


def test_idle_watchdog_kill_does_not_reset_session() -> None:
    exc = IdleWatchdogKilledError(reason="idle", signal=15)
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.reset_session is False


def test_oserror_etimedout_still_classified_as_environmental() -> None:
    exc = OSError(errno.ETIMEDOUT, "timed out")
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.category == FailureCategory.ENVIRONMENTAL


def test_connection_error_with_timeout_text_still_environmental() -> None:
    exc = ConnectionError("connection timed out")
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.category == FailureCategory.ENVIRONMENTAL


def test_idle_watchdog_kill_takes_precedence_over_environmental_check() -> None:
    exc = IdleWatchdogKilledError(reason="idle", signal=15)
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.category == FailureCategory.AGENT


def test_socket_timeout_still_environmental() -> None:
    exc = TimeoutError("timed out")
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.category == FailureCategory.ENVIRONMENTAL


def test_idle_watchdog_kill_classifier_uses_typed_signal_and_reason() -> None:
    exc = IdleWatchdogKilledError(reason="specific_test_reason_xyz", signal=15)
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.category == FailureCategory.AGENT


def test_idle_watchdog_kill_subclass_with_misleading_str_still_agent() -> None:

    class _MisleadingIdleKillError(IdleWatchdogKilledError):
        def __str__(self) -> str:
            return "ConnectionError: transient timeout blip"

    exc = _MisleadingIdleKillError(reason="idle", signal=15)
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.category == FailureCategory.AGENT, (
        f"subclass with misleading __str__ must still classify as AGENT, got {classified.category}"
    )


def test_idle_watchdog_kill_carries_typed_attributes() -> None:
    exc = IdleWatchdogKilledError(reason="idle", signal=15)
    assert exc.reason == "idle"
    assert exc.signal == 15


def test_idle_watchdog_kill_preserves_attrs_in_subclass() -> None:

    class _IdleKillError(IdleWatchdogKilledError):
        pass

    exc = _IdleKillError(reason="stalled", signal=9)
    assert exc.reason == "stalled"
    assert exc.signal == 9


@pytest.mark.parametrize(
    "reason",
    ["idle", "stalled", "no_output", "post_tool_stall", "subprocess_stall"],
)
def test_idle_watchdog_kill_various_reasons_all_classify_as_agent(
    reason: str,
) -> None:
    exc = IdleWatchdogKilledError(reason=reason, signal=15)
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.category == FailureCategory.AGENT
