from __future__ import annotations

from loguru import logger

from ralph.agents.invoke import OpenCodeResumableExitError
from ralph.recovery.classifier import FailureCategory, FailureClassifier


class _CapturedLogs:
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
    classifier = FailureClassifier()
    exc = OpenCodeResumableExitError("opencode", session_id="abc123")

    failure = classifier.classify(exc, phase="development", agent="opencode")

    assert failure.category == FailureCategory.AGENT, (
        f"OpenCodeResumableExitError MUST classify as AGENT; got {failure.category!r}"
    )
    assert failure.counts_against_budget is True
    assert failure.reset_session is False


def test_opencode_resumable_exit_resume_session_id_forwarded() -> None:
    classifier = FailureClassifier()
    exc = OpenCodeResumableExitError("opencode", session_id="abc123")
    assert exc.resumable_session_id == "abc123"

    failure = classifier.classify(exc, phase="development", agent="opencode")

    assert exc.resumable_session_id == "abc123", (
        "classifier MUST NOT mutate the exception's resumable_session_id"
    )
    assert failure.category == FailureCategory.AGENT


def test_opencode_resumable_exit_attributed_to_agent() -> None:
    classifier = FailureClassifier()
    exc = OpenCodeResumableExitError("opencode-minimax-coding-plan", session_id="xyz789")

    failure = classifier.classify(exc, phase="development", agent="opencode")

    assert failure.attributed_agent == "opencode"
    assert failure.attributed_phase == "development"
    assert failure.category == FailureCategory.AGENT


def test_opencode_resumable_exit_does_not_trigger_ambiguous_warning() -> None:
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
    classifier = FailureClassifier()
    exc = OpenCodeResumableExitError("opencode", session_id=None)

    failure = classifier.classify(exc, phase="development", agent="opencode")

    assert failure.category == FailureCategory.AGENT
    assert failure.counts_against_budget is True
    assert failure.reset_session is False


def test_diagnostic_context_carried() -> None:
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

    assert failure.category == FailureCategory.AGENT
    assert failure.category != FailureCategory.AMBIGUOUS
    assert failure.resumable_session_id == "sess-1"

    assert exc.last_observed_tool_call == "read_file"
    assert exc.last_evidence_summary == "workspace_change: kind=source weight=1.0"
    assert exc.elapsed_seconds == 420.0
    assert len(exc.transcript_tail) == 3
    assert exc.transcript_tail == ("line-1", "line-2", "line-3")

    message = str(exc)
    assert "read_file" in message, (
        f"exception message MUST carry the last_tool_call diagnostic; got {message!r}"
    )
    assert "420.0s" in message, (
        f"exception message MUST carry the elapsed diagnostic; got {message!r}"
    )


def test_backward_compatible_construction() -> None:
    exc = OpenCodeResumableExitError("opencode", session_id="sess-legacy")

    assert exc.resumable_session_id == "sess-legacy"

    assert exc.last_observed_tool_call is None
    assert exc.last_evidence_summary is None
    assert exc.elapsed_seconds is None
    assert exc.transcript_tail == ()

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
