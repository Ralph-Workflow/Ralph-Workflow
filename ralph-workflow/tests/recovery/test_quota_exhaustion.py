"""Black-box regressions for terminal agent quota exhaustion.

KEEP: existing unavailable-agent tests cover temporary provider limits. This
module covers the distinct terminal failure contract: recognized quota output
must name its agent, avoid authentication/timeout/ambiguous routing, skip a
same-agent retry, and be surfaced by the completion gate.
"""

from __future__ import annotations

from typing import IO

import pytest

from ralph.agents.invoke import AgentInvocationError, check_process_result
from ralph.pipeline.agent_retry_decision import resolve_retry_intent
from ralph.process.manager import ManagedProcess
from ralph.recovery.classifier import FailureCategory, FailureClassifier


class _CompletedProcess(ManagedProcess):
    def __init__(self, returncode: int, stderr: str) -> None:
        self._completed_returncode = returncode
        self._ralph_bounded_stderr = stderr

    @property
    def returncode(self) -> int:
        return self._completed_returncode

    @property
    def stderr(self) -> IO[bytes] | None:
        return None

    def termination_issuer(self) -> None:
        return None


@pytest.mark.parametrize(
    ("agent", "line"),
    (
        ("agy", "RESOURCE_EXHAUSTED (code 429)"),
        ("agy", "API quota exhausted"),
        ("agy", "Individual quota reached"),
        ("claude", "You've hit your session limit"),
        ("codex", "you exceeded your current quota"),
        ("claude", "rate limit reached"),
        ("codex", "resource exhausted"),
    ),
    ids=(
        "resource-exhausted-429",
        "api-quota-exhausted",
        "individual-quota-reached",
        "session-limit",
        "current-quota",
        "rate-limit",
        "resource-exhausted",
    ),
)
def test_recognized_quota_output_is_terminal_for_the_affected_agent(
    agent: str,
    line: str,
) -> None:
    exc = AgentInvocationError(agent, 1, line)

    classified = FailureClassifier().classify(
        exc,
        phase="development",
        agent=agent,
        connectivity_state="online",
    )
    intent = resolve_retry_intent(
        exc,
        phase="development",
        agent=agent,
        session_id="session-1",
        inactivity_error_type=RuntimeError,
    )

    assert intent is not None
    assert intent.skip_same_agent_retries is True
    assert classified.category == FailureCategory.AGENT
    assert agent in classified.reason
    assert "quota or rate limit is exhausted" in classified.reason
    assert line in classified.reason


@pytest.mark.parametrize(
    "line",
    (
        "Failed to get OAuth token",
        "You are not logged into Antigravity\nChainedAuth succeeded",
        "connection reset by peer",
        "timed out with no output",
        "The project documentation uses the word quota incidentally.",
        "authentication failed",
        "mock AGY unknown failure",
    ),
)
def test_non_quota_output_is_not_routed_as_terminal_quota(line: str) -> None:
    classified = FailureClassifier().classify(
        AgentInvocationError("agy", 1, line),
        phase="development",
        agent="agy",
        connectivity_state="online",
    )

    assert "quota or rate limit is exhausted" not in classified.reason
    assert line in classified.reason


def test_completion_gate_reports_quota_before_missing_completion_evidence() -> None:
    with pytest.raises(AgentInvocationError) as excinfo:
        check_process_result(
            _CompletedProcess(1, "RESOURCE_EXHAUSTED (code 429)"),
            "agy",
            ["RESOURCE_EXHAUSTED (code 429)"],
        )

    classified = FailureClassifier().classify(
        excinfo.value,
        phase="development",
        agent="agy",
        connectivity_state="online",
    )

    assert "agy" in str(excinfo.value)
    assert "RESOURCE_EXHAUSTED (code 429)" in str(excinfo.value)
    assert "quota or rate limit is exhausted" in str(excinfo.value)
    assert classified.category == FailureCategory.AGENT
    assert classified.category == FailureCategory.AGENT


def test_completion_gate_reports_provider_quota_even_after_clean_process_exit() -> None:
    with pytest.raises(AgentInvocationError) as excinfo:
        check_process_result(
            _CompletedProcess(0, ""),
            "cursor",
            ["RetriableError: [resource_exhausted] Error; quota or rate limit is exhausted"],
        )

    assert "quota or rate limit is exhausted" in str(excinfo.value)


def test_completion_gate_ignores_quota_text_inside_echoed_user_event() -> None:
    with pytest.raises(AgentInvocationError) as excinfo:
        check_process_result(
            _CompletedProcess(1, ""),
            "cursor",
            ['{"type":"user","message":{"role":"user","content":"quota exhausted"}}'],
        )

    assert "quota or rate limit is exhausted" not in str(excinfo.value)
