"""Black-box tests for the R7 deterministic rc=0 classification contract.

R7 (Trustworthy Idle Watchdog product spec):

    Ambiguous rc=0 exits are root-caused, deterministically classified,
    and handled.

The product spec cites the ``OpenCodeResumableExitError`` pattern:

    Agents sometimes exit cleanly (rc=0) but with no completion evidence:
    ``Agent 'opencode' failed with code 0: agent session exited without
    required completion evidence (no artifact, no declare_complete)
    [flagged_for_review=true]``.

The fix is a deterministic typed-cause branch in
``ralph/recovery/failure_classifier.py:_categorize_exc`` (lines 859-869)
that classifies ``OpenCodeResumableExitError`` as
``FailureCategory.AGENT`` BEFORE the broader ``AgentInvocationError``
branch. The error NEVER falls to ``FailureCategory.AMBIGUOUS``.

These tests are pure black-box: no real subprocess, no real time, no
real filesystem. The classifier is exercised with synthetic exception
instances.
"""

from __future__ import annotations

from ralph.agents.invoke._open_code_resumable_exit_error import (
    OpenCodeResumableExitError,
)
from ralph.agents.invoke._session_resume import recovery_action_for_failure_reason
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import FailureClassifier


def test_opencode_resumable_exit_error_classifies_as_agent() -> None:
    """R7: ``OpenCodeResumableExitError`` classifies as ``FailureCategory.AGENT``.

    The product spec cites the ``OpenCodeResumableExitError`` as the
    headline pattern that MUST be classified deterministically. The
    typed-cause branch in ``_categorize_exc`` (lines 859-869) applies
    BEFORE the broader ``AgentInvocationError`` branch so the exception
    NEVER falls to ``FailureCategory.AMBIGUOUS``.
    """
    exc = OpenCodeResumableExitError(agent_name="opencode", session_id="sess-xyz")
    classifier = FailureClassifier()
    failure = classifier.classify(
        exc,
        phase="development",
        agent="opencode",
        connectivity_state="online",
    )
    assert failure.category == FailureCategory.AGENT
    # The ``resumable_session_id`` attribute MUST propagate from the
    # typed exception to the ClassifiedFailure so the recovery
    # controller can thread it forward as a resume intent.
    assert failure.resumable_session_id == "sess-xyz"
    # The failure counts against the budget (an agent exit-without-
    # completion is a real failure, not a recoverable artifact
    # validation problem).
    assert failure.counts_against_budget is True
    # ``reset_session=False`` -- this is a resume-friendly exit, not
    # a stale-session reset. The recovery controller must continue the
    # existing session, not start a new one.
    assert failure.reset_session is False


def test_opencode_resumable_exit_error_does_not_flag_ambiguous() -> None:
    """R7: the error MUST NOT be flagged as ambiguous.

    The pre-fix behavior emitted ``flagged_for_review=true`` as a
    noisy warning on every ambiguous rc=0 exit. The fix: the typed-
    cause branch classifies deterministically so the warning is no
    longer needed. The classifier result MUST be ``FailureCategory.AGENT``
    and the recovery controller MUST handle it via the resume path
    (no ambiguous warning).
    """
    exc = OpenCodeResumableExitError(agent_name="opencode", session_id="sess-amb-1")
    classifier = FailureClassifier()
    failure = classifier.classify(
        exc,
        phase="development",
        agent="opencode",
        connectivity_state="online",
    )
    # The headline assertion: the typed-cause branch classifies as
    # AGENT, NOT AMBIGUOUS.
    assert failure.category != FailureCategory.AMBIGUOUS
    assert failure.category == FailureCategory.AGENT


def test_recovery_action_for_resumable_exit_returns_resume() -> None:
    """R7: the recovery action is ``resume`` when a prior session exists.

    The ``OpenCodeResumableExitError`` is a deterministic rc=0 exit
    with no completion evidence. The recovery action MUST continue
    the existing session (the work the agent was doing is recoverable)
    rather than starting a fresh session and re-reading the prompt.
    """
    action = recovery_action_for_failure_reason(
        "OpenCodeResumableExitError",
        has_prior_session=True,
    )
    assert action == "resume"


def test_recovery_action_for_resumable_exit_returns_fresh_when_no_prior_session() -> None:
    """R7: no prior session -> 'fresh' (deliberate phase transition).

    Without a prior session to resume, the recovery action MUST be
    ``'fresh'`` -- the agent starts a brand-new session. The fresh
    path goes through ``fresh_session_options`` which clears the
    session id.
    """
    action = recovery_action_for_failure_reason(
        "OpenCodeResumableExitError",
        has_prior_session=False,
    )
    assert action == "fresh"


def test_opencode_resumable_exit_error_is_not_ambiguous() -> None:
    """R7: every instance classifies as AGENT, NEVER AMBIGUOUS.

    The pre-fix behavior let the broader ``AgentInvocationError``
    branch classify as ``AMBIGUOUS`` (with a noisy
    ``flagged_for_review=true`` warning). The fix: the typed-cause
    branch is BEFORE the broader branch so EVERY instance of
    ``OpenCodeResumableExitError`` classifies as ``AGENT``.
    """
    for session_id in ("sess-a", "sess-b", "sess-c", None):
        exc = OpenCodeResumableExitError(agent_name="opencode", session_id=session_id)
        classifier = FailureClassifier()
        failure = classifier.classify(
            exc,
            phase="development",
            agent="opencode",
            connectivity_state="online",
        )
        assert failure.category == FailureCategory.AGENT, (
            f"OpenCodeResumableExitError with session_id={session_id!r}"
            " must classify as AGENT (not AMBIGUOUS)"
        )
        assert failure.category != FailureCategory.AMBIGUOUS


def test_resumable_session_id_propagates_from_typed_exception() -> None:
    """R7: ``resumable_session_id`` propagates from the typed exception.

    The classifier lifts ``resumable_session_id`` from the typed
    exception's attribute via ``getattr(exc_obj,
    "resumable_session_id", None)`` so the recovery controller can
    thread it forward as a resume intent.
    """
    exc = OpenCodeResumableExitError(agent_name="opencode", session_id="sess-typed-attr")
    # Verify the typed exception carries the attribute.
    assert exc.resumable_session_id == "sess-typed-attr"
    classifier = FailureClassifier()
    failure = classifier.classify(
        exc,
        phase="development",
        agent="opencode",
        connectivity_state="online",
    )
    # The classifier lifts the attribute end-to-end.
    assert failure.resumable_session_id == "sess-typed-attr"
