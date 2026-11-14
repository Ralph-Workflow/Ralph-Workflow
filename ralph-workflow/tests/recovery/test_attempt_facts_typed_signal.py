from __future__ import annotations

from ralph.agents.invoke import (
    BrokenAgentExitError,
    MissingCredentialsError,
    QuotaExhaustedError,
)
from ralph.agents.invoke._completion_termination import attempt_facts_for_failure


def test_quota_exhausted_error_carries_typed_signal_fact() -> None:
    facts = attempt_facts_for_failure(QuotaExhaustedError("claude", "weekly limit hit"))

    assert facts.typed_signal is not None
    assert facts.typed_signal.name == "QuotaExhaustedError"
    assert facts.typed_signal.provider_originated is True


def test_missing_credentials_error_carries_typed_signal_fact() -> None:
    facts = attempt_facts_for_failure(
        MissingCredentialsError("claude", "ANTHROPIC_API_KEY not set")
    )

    assert facts.typed_signal is not None
    assert facts.typed_signal.name == "MissingCredentialsError"
    assert facts.typed_signal.provider_originated is True


def test_broken_agent_exit_error_carries_typed_signal_fact() -> None:
    facts = attempt_facts_for_failure(BrokenAgentExitError("claude", reason="prompt_echo"))

    assert facts.typed_signal is not None
    assert facts.typed_signal.name == "BrokenAgentExitError"
    assert facts.typed_signal.provider_originated is True


def test_broken_agent_exit_without_prompt_echo_is_agent_originated() -> None:
    facts = attempt_facts_for_failure(BrokenAgentExitError("claude", reason="no_output"))

    assert facts.typed_signal is not None
    assert facts.typed_signal.name == "BrokenAgentExitError"
    assert facts.typed_signal.provider_originated is False


def test_agent_initiated_typed_signal_for_agent_terminated_attempts() -> None:
    facts = attempt_facts_for_failure(QuotaExhaustedError("claude", "weekly limit hit"))

    assert facts.typed_signal is not None
    assert facts.typed_signal.agent_initiated is True


def test_unrelated_failure_has_no_typed_signal_fact() -> None:
    assert attempt_facts_for_failure(RuntimeError("boom")).typed_signal is None


def test_same_named_plain_exception_has_no_typed_signal_fact() -> None:
    class QuotaExhaustedError(RuntimeError):
        pass

    assert attempt_facts_for_failure(QuotaExhaustedError("boom")).typed_signal is None
