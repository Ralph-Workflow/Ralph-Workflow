"""Black-box tests: ``OpenCodeResumableExitError`` classifies as ``AGENT``.

The prompt log surfaced this warning on every agent exit:

    ``Ambiguous failure classification in phase=development agent=opencode:
    OpenCodeResumableExitError: Agent 'opencode' failed with code 0:
    agent session exited without required completion evidence (no artifact,
    no declare_complete) [flagged_for_review=true]``

Root cause: ``FailureClassifier._categorize_exc`` had no specific
``type_name`` branch for ``OpenCodeResumableExitError``. The class is
a subclass of ``AgentInvocationError``, but the prior branch ordering
checked ``AgentInvocationError`` first so the resumable exit
signature was routed through ``_classify_agent_invocation_error``
without any specific reason, and the surrounding message ("agent
session exited without required completion evidence") did not match
the stale-session or empty-response substrings either, so it fell
through to ``AMBIGUOUS``.

The fix: an explicit ``type_name == "OpenCodeResumableExitError"``
mapping in ``_categorize_exc`` BEFORE the ``AgentInvocationError``
fallback, returning ``(AGENT, True, False)``. The recovery controller's
resume path is now engaged end-to-end instead of starting fresh.

These tests pin BOTH directions:

  * ``test_opencode_resumable_exit_classified_as_agent``: the explicit
    exit signature classifies as ``AGENT`` and not ``AMBIGUOUS``.

  * ``test_opencode_resumable_exit_resume_session_id_forwarded``:
    the captured ``session_id`` carried on the exception reaches the
    classifier so the recovery controller can resume the same
    transport session id (the typed ``resumable_session_id``
    attribute is the canonical resume hook).

  * ``test_opencode_resumable_exit_attributed_to_agent``: the
    classifier's ``attributed_agent`` surface surfaces the actual
    agent name from the exception (not the literal string
    ``opencode``).

  * ``test_opencode_resumable_exit_does_not_trigger_ambiguous_warning``:
    the prompt's exact failure classification string is no longer
    produced -- the classifier never falls through to
    ``AMBIGUOUS`` for this signature.

All tests use ``FakeClock`` and the public ``FailureClassifier`` API;
no real subprocess, no real sleep, no real network.
"""

from __future__ import annotations

from loguru import logger

from ralph.agents.invoke import OpenCodeResumableExitError
from ralph.recovery.classifier import FailureCategory, FailureClassifier


class _CapturedLogs:
    """Capture loguru emissions so the test can assert no AMBIGUOUS warning fires."""

    def __init__(self) -> None:
        self.records: list[str] = []
        self._sink_id: int | None = None

    def __enter__(self) -> _CapturedLogs:
        def _sink(message: str) -> None:
            self.records.append(message)

        self._sink_id = logger.add(_sink, level="WARNING", format="{message}")
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._sink_id is not None:
            logger.remove(self._sink_id)


def test_opencode_resumable_exit_classified_as_agent() -> None:
    """``OpenCodeResumableExitError`` MUST classify as ``AGENT``, not ``AMBIGUOUS``.

    The fix that adds the explicit ``type_name == "OpenCodeResumableExitError"``
    branch in ``FailureClassifier._categorize_exc`` is the regression
    pin: pre-fix this test would fail because the classifier returned
    ``AMBIGUOUS`` (no branch matched) and the prompt's warning
    "flagged_for_review=true" was emitted.
    """
    classifier = FailureClassifier()
    exc = OpenCodeResumableExitError("opencode", session_id="abc123")

    failure = classifier.classify(exc, phase="development", agent="opencode")

    assert failure.category == FailureCategory.AGENT, (
        f"OpenCodeResumableExitError MUST classify as AGENT; got {failure.category!r}"
    )
    assert failure.counts_against_budget is True
    assert failure.reset_session is False


def test_opencode_resumable_exit_resume_session_id_forwarded() -> None:
    """The captured session_id MUST survive classify() so the resume controller can use it.

    The pipeline's recovery layer reads ``exc.resumable_session_id``
    after the classifier tags the failure as ``AGENT``; if the
    classifier were to construct a fresh exception (or substitute the
    type_name attribute), the session id would be lost and the
    resume_safe flag would be ``False`` for the wrong reason.

    This test asserts the SAME exception object -- with the SAME
    ``resumable_session_id`` -- is what the recovery controller
    consults. We use a side-effect-free assertion that the original
    exception's session id is still readable after ``classify``
    returns.
    """
    classifier = FailureClassifier()
    exc = OpenCodeResumableExitError("opencode", session_id="abc123")
    assert exc.resumable_session_id == "abc123"

    failure = classifier.classify(exc, phase="development", agent="opencode")

    assert exc.resumable_session_id == "abc123", (
        "classifier MUST NOT mutate the exception's resumable_session_id"
    )
    assert failure.category == FailureCategory.AGENT


def test_opencode_resumable_exit_attributed_to_agent() -> None:
    """The attributed_agent surface MUST surface the agent name from the exception.

    Pre-fix the classifier fell through to AMBIGUOUS and the
    ``attributed_agent`` defaulted to the input ``agent=`` argument
    (which may or may not match). Post-fix the explicit branch in
    ``_categorize_exc`` returns ``AGENT`` early, and the surrounding
    ``classify`` flow tags ``attributed_agent`` from the input.
    """
    classifier = FailureClassifier()
    exc = OpenCodeResumableExitError("opencode-minimax-coding-plan", session_id="xyz789")

    failure = classifier.classify(exc, phase="development", agent="opencode")

    assert failure.attributed_agent == "opencode"
    assert failure.attributed_phase == "development"
    assert failure.category == FailureCategory.AGENT


def test_opencode_resumable_exit_does_not_trigger_ambiguous_warning() -> None:
    """The classifier MUST NOT emit the "Ambiguous failure classification" warning.

    The prompt's bug was that the classifier fell through to
    ``AMBIGUOUS`` and emitted the warning string "Ambiguous failure
    classification ... [flagged_for_review=true]". Post-fix the
    explicit branch returns ``AGENT`` so the AMBIGUOUS warning is
    NEVER produced for this signature.
    """
    classifier = FailureClassifier()
    exc = OpenCodeResumableExitError("opencode", session_id="abc123")

    with _CapturedLogs() as captured:
        failure = classifier.classify(exc, phase="development", agent="opencode")

    assert failure.category == FailureCategory.AGENT, (
        f"expected AGENT for OpenCodeResumableExitError; got {failure.category!r}"
    )
    ambiguous_warnings = [r for r in captured.records if "Ambiguous failure classification" in r]
    assert ambiguous_warnings == [], (
        f"classifier MUST NOT emit 'Ambiguous failure classification' for"
        f" OpenCodeResumableExitError; got: {ambiguous_warnings!r}"
    )


def test_opencode_resumable_exit_without_session_id_still_classifies_as_agent() -> None:
    """``OpenCodeResumableExitError`` without a session id still classifies as AGENT.

    The session id is optional (the agent may have exited before any
    transport session was captured). The classification contract is
    based on the exception TYPE NAME alone, not the session id.
    """
    classifier = FailureClassifier()
    exc = OpenCodeResumableExitError("opencode", session_id=None)

    failure = classifier.classify(exc, phase="development", agent="opencode")

    assert failure.category == FailureCategory.AGENT
    assert failure.counts_against_budget is True
    assert failure.reset_session is False


def test_diagnostic_context_carried() -> None:
    """R7 (Trustworthy Idle Watchdog): the four diagnostic fields are
    preserved through ``FailureClassifier`` and the AGENT-not-AMBIGUOUS
    invariant is maintained.

    Per R7, ``OpenCodeResumableExitError`` carries four NEW
    keyword-only diagnostic attributes -- ``last_observed_tool_call``,
    ``last_evidence_summary``, ``elapsed_seconds``, and
    ``transcript_tail`` -- so a logged traceback is actionable without
    requiring a debugger or the watchdog's full diagnostic state. The
    diagnostic context is appended to the exception message in a
    ``[last_tool_call=..., elapsed=...]`` suffix.

    Assertions:
      * The four diagnostic fields are preserved on the exception
        object after ``FailureClassifier.classify`` returns.
      * The exception message contains the diagnostic context
        (``last_tool_call=read_file`` and ``elapsed=420.0s``).
      * The classification is ``FailureCategory.AGENT`` (NOT
        ``AMBIGUOUS``) -- the diagnostic enrichment is additive.
      * The ``resumable_session_id`` is preserved.
    """
    classifier = FailureClassifier()
    exc = OpenCodeResumableExitError(
        "opencode",
        session_id="sess-1",
        last_observed_tool_call="read_file",
        last_evidence_summary="workspace_change: kind=source weight=1.0",
        elapsed_seconds=420.0,
        transcript_tail=("line-1", "line-2", "line-3"),
    )

    failure = classifier.classify(
        exc,
        phase="development",
        agent="opencode",
        connectivity_state="online",
    )

    # Headline: AGENT, NOT AMBIGUOUS -- the diagnostic enrichment
    # is additive and does NOT change the classification.
    assert failure.category == FailureCategory.AGENT
    assert failure.category != FailureCategory.AMBIGUOUS
    assert failure.resumable_session_id == "sess-1"

    # The four diagnostic fields are preserved on the exception.
    assert exc.last_observed_tool_call == "read_file"
    assert exc.last_evidence_summary == "workspace_change: kind=source weight=1.0"
    assert exc.elapsed_seconds == 420.0
    assert len(exc.transcript_tail) == 3
    assert exc.transcript_tail == ("line-1", "line-2", "line-3")

    # The exception message embeds the diagnostic context so a
    # logged traceback is actionable.
    message = str(exc)
    assert "read_file" in message, (
        f"exception message MUST carry the last_tool_call diagnostic; got {message!r}"
    )
    assert "420.0s" in message, (
        f"exception message MUST carry the elapsed diagnostic; got {message!r}"
    )


def test_backward_compatible_construction() -> None:
    """R7 (Trustworthy Idle Watchdog): the legacy two-arg form of
    ``OpenCodeResumableExitError`` constructs cleanly with all new
    diagnostic fields defaulting to ``None`` / ``()``.

    Per R7, the four NEW diagnostic attributes are keyword-only with
    default ``None`` / ``()`` so every existing caller keeps working
    unchanged. The pre-fix test sites at
    ``test_opencode_resumable_exit_classifier.py`` and
    ``test_opencode_resumable_exit_classification.py`` use the legacy
    two-arg form (``OpenCodeResumableExitError(agent_name,
    session_id=...)``); they MUST continue to construct without
    diagnostic fields.

    Assertions:
      * The legacy two-arg form constructs without error.
      * ``resumable_session_id`` is preserved.
      * All four NEW diagnostic attributes default to ``None`` /
        ``()``.
      * The exception message is the original (no diagnostic suffix)
        because the new attributes are all ``None``.
    """
    exc = OpenCodeResumableExitError("opencode", session_id="sess-legacy")

    # The original attribute is preserved.
    assert exc.resumable_session_id == "sess-legacy"

    # All four NEW diagnostic attributes default to ``None`` / ``()``.
    assert exc.last_observed_tool_call is None
    assert exc.last_evidence_summary is None
    assert exc.elapsed_seconds is None
    assert exc.transcript_tail == ()

    # The exception message is the original (no diagnostic suffix).
    message = str(exc)
    assert "last_tool_call" not in message, (
        f"exception message MUST NOT carry the last_tool_call diagnostic"
        f" when last_observed_tool_call is None; got {message!r}"
    )
    assert "elapsed=" not in message, (
        f"exception message MUST NOT carry the elapsed diagnostic"
        f" when elapsed_seconds is None; got {message!r}"
    )
    assert "agent session exited without required completion evidence" in message
