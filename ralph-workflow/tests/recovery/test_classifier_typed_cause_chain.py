"""Property test: the failure classifier walks the full __cause__ chain.

The watchdog fire path wraps the typed ``IdleWatchdogKilledError``
inside ``_IdleStreamTimeoutError`` (which is then converted to
``AgentInactivityTimeoutError`` by the recovery layer). On the real
runtime path the chain is:

  AgentInactivityTimeoutError
    \u2191 __cause__
    _IdleStreamTimeoutError
      \u2191 __cause__
      IdleWatchdogKilledError (typed, has .reason and .signal)

The classifier's typed-attribute branch must reach the typed exception
even when it is two or three hops deep in the chain. A regression that
checked only the first ``__cause__`` would let the AGENT classification
fall through to text-based matching, which can mislabel the SIGTERM as
ENVIRONMENTAL because the watchdog's message happens to contain the
word "timeout".

These tests use synthetic exception classes (via ``__cause__`` and
``__context__``) so they do NOT depend on the runtime exception
classes (avoiding a circular import through ``ralph.agents.invoke``).
They run in <1s with no real I/O.
"""

from __future__ import annotations

import errno

from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import FailureClassifier


class _IdleStreamTimeoutError(Exception):
    """Simulates the watchdog's internal _IdleStreamTimeoutError wrapper.

    The real exception class lives in ``ralph.agents.invoke._errors``
    and depends on a wider surface (transcript parsing, recovery
    contracts). We use a stand-in here so this test does not pull
    in the runtime exception classes (which would create a circular
    import through ``ralph.agents.invoke`` and ``ralph.recovery``).
    """

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        if cause is not None:
            self.__cause__ = cause


class _AgentInactivityTimeoutError(Exception):
    """Simulates the recovery layer's AgentInactivityTimeoutError wrapper."""

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        if cause is not None:
            self.__cause__ = cause


def test_classifier_reaches_typed_cause_one_hop_deep() -> None:
    """The typed watchdog cause is reachable when it is one hop deep.

    Real path: ``_IdleStreamTimeoutError`` whose ``__cause__`` is the
    typed ``IdleWatchdogKilledError``. The classifier must classify
    as AGENT, not ENVIRONMENTAL.
    """
    typed = IdleWatchdogKilledError(reason="idle", signal=15)
    wrapper = _IdleStreamTimeoutError("watchdog fired", cause=typed)
    classified = FailureClassifier().classify(
        wrapper, phase="p", agent="a", connectivity_state="online"
    )
    assert classified.category == FailureCategory.AGENT, (
        f"typed cause one hop deep must classify as AGENT, got {classified.category}"
    )


def test_classifier_reaches_typed_cause_two_hops_deep() -> None:
    """The typed watchdog cause is reachable when it is two hops deep.

    Real path: ``AgentInactivityTimeoutError`` (recovery layer)
    whose ``__cause__`` is ``_IdleStreamTimeoutError`` (watchdog's
    internal wrapper) whose ``__cause__`` is the typed
    ``IdleWatchdogKilledError``. The classifier must classify as
    AGENT, not ENVIRONMENTAL, even though the typed cause is
    buried two layers deep in the chain.
    """
    typed = IdleWatchdogKilledError(reason="no_output", signal=15)
    stream_timeout = _IdleStreamTimeoutError("watchdog fired", cause=typed)
    agent_inactivity = _AgentInactivityTimeoutError(
        "agent idle for too long", cause=stream_timeout
    )
    classified = FailureClassifier().classify(
        agent_inactivity, phase="p", agent="a", connectivity_state="online"
    )
    assert classified.category == FailureCategory.AGENT, (
        f"typed cause two hops deep must classify as AGENT, got {classified.category}"
    )


def test_classifier_reaches_typed_cause_via_context_chain() -> None:
    """The typed watchdog cause is reachable via ``__context__`` as well.

    When a bare ``raise`` (no ``from``) is used, the implicit cause
    is set on ``__context__`` instead of ``__cause__``. The classifier
    must walk both chains so the typed cause is reachable in either
    case.
    """
    typed = IdleWatchdogKilledError(reason="stalled", signal=15)
    # Simulate ``raise wrapper from None`` then ``raise top`` (the
    # context chain is set automatically when a new exception is
    # raised during the handling of an existing one).
    wrapper = _IdleStreamTimeoutError("watchdog fired")
    wrapper.__context__ = typed
    classified = FailureClassifier().classify(wrapper, phase="p", agent="a")
    assert classified.category == FailureCategory.AGENT, (
        f"typed cause via __context__ must classify as AGENT, got {classified.category}"
    )


def test_classifier_no_typed_cause_falls_through_to_text() -> None:
    """Without a typed watchdog cause, the classifier uses text-based matching.

    Regression guard: the typed-cause walk must not change the
    classifier's behavior for non-watchdog failures. An
    ``OSError(ETIMEDOUT)`` is still ENVIRONMENTAL.
    """
    exc = OSError(errno.ETIMEDOUT, "timed out")
    classified = FailureClassifier().classify(exc, phase="p", agent="a")
    assert classified.category == FailureCategory.ENVIRONMENTAL, (
        f"OSError(ETIMEDOUT) without typed cause must classify as ENVIRONMENTAL, "
        f"got {classified.category}"
    )


def test_classifier_typed_cause_cycle_does_not_hang() -> None:
    """A cyclic cause chain does not hang the classifier.

    The visited-set guard bounds the walk so a malformed cycle in the
    chain cannot deadlock the classifier. The test builds a small
    cycle (typed \u2192 wrapper \u2192 typed) and verifies that classification
    completes (returns AGENT, since the typed cause is found) without
    timing out.
    """
    typed = IdleWatchdogKilledError(reason="idle", signal=15)
    wrapper = _IdleStreamTimeoutError("watchdog fired", cause=typed)
    typed.__cause__ = wrapper  # create the cycle
    try:
        classified = FailureClassifier().classify(wrapper, phase="p", agent="a")
        assert classified.category == FailureCategory.AGENT
    finally:
        # break the cycle so the test teardown can garbage-collect the
        # exception objects
        typed.__cause__ = None
