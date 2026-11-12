from __future__ import annotations

import pytest

from ralph.agents.invoke import AgentInvocationError, attempt_facts_for_failure
from ralph.config.models import UnifiedConfig
from ralph.pipeline.agent_retry_decision import resolve_retry_intent
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.attribution import RuntimeCause, attribute_attempt
from ralph.recovery.canary_probe import ProbeOutcome
from ralph.recovery.dead_agent_probe import production_dead_agent_probe_runner
from ralph.recovery.dead_agent_verdict import (
    DeadAgentVerdict,
    decide_dead_agent_verdict,
    runtime_issued_termination_is_not_agent_fault,
)
from ralph.recovery.failure_classifier import FailureClassifier
from ralph.recovery.unavailability_reason import UnavailabilityReason

PHASE = "development"
AGENT = "agent_a"
LIMIT = "You've hit your session limit"


def _watchdog_facts(*, live: bool) -> AttemptFacts:
    return AttemptFacts(
        tool_calls=("read_file",) if live else (),
        artifact_valid=False,
        liveness_observed=live,
        termination=TerminationRecord.create(
            TerminationIssuer.POST_EXIT_WATCHDOG,
            observed_returncode=-15,
            reported_returncode=0,
            liveness_observed=live,
        ),
    )


def test_a_post_exit_watchdog_kill_of_a_live_session_is_labelled_a_runtime_cause() -> None:
    verdict = attribute_attempt(_watchdog_facts(live=True))

    assert verdict.runtime_cause is RuntimeCause.KILL_POST_EXIT_WATCHDOG
    assert verdict.counts_toward_agent_state is False


@pytest.mark.parametrize("probe", [*list(ProbeOutcome), None])
def test_a_post_exit_watchdog_end_is_never_the_agents_fault_whatever_the_probe_says(
    probe: ProbeOutcome | None,
) -> None:
    facts = _watchdog_facts(live=True)

    assert runtime_issued_termination_is_not_agent_fault(facts) is True
    verdict = decide_dead_agent_verdict(facts, attribute_attempt(facts), probe)
    assert verdict.verdict is DeadAgentVerdict.NOT_AGENT_FAULT


def test_tool_output_in_the_transcript_never_marks_the_agent_unavailable_for_limits() -> None:
    exc = AgentInvocationError(
        AGENT, 1, stderr="", parsed_output=[f'{{"type":"tool_result","content":"{LIMIT}"}}']
    )

    classified = FailureClassifier().classify(
        exc, phase=PHASE, agent=AGENT, connectivity_state="online"
    )

    assert classified.unavailability_reason is not UnavailabilityReason.OUT_OF_CREDITS
    assert "quota or rate limit is exhausted" not in classified.reason


def test_a_limit_phrase_on_stderr_is_still_a_hint_for_the_classifier() -> None:
    exc = AgentInvocationError(AGENT, 1, stderr=LIMIT)

    classified = FailureClassifier().classify(
        exc, phase=PHASE, agent=AGENT, connectivity_state="online"
    )

    assert "quota or rate limit is exhausted" in classified.reason


def test_a_transcript_only_limit_phrase_does_not_select_the_skip_route() -> None:
    exc = AgentInvocationError(AGENT, 1, stderr="", parsed_output=[LIMIT])

    intent = resolve_retry_intent(
        exc, phase=PHASE, agent=AGENT, session_id=None, inactivity_error_type=TimeoutError
    )

    assert intent is None or intent.skip_same_agent_retries is False


def test_the_shared_probe_runner_helper_is_absent_without_a_config_and_present_with_one() -> None:
    assert production_dead_agent_probe_runner(None) is None
    assert callable(production_dead_agent_probe_runner(UnifiedConfig.model_validate({})))


@pytest.mark.parametrize("code", [-9, -15, -11, 129, 137, 143, 159])
def test_a_signal_shaped_return_code_is_never_attributed_to_an_agent_exit(code: int) -> None:
    facts = attempt_facts_for_failure(AgentInvocationError(AGENT, code))

    assert facts.termination is not None
    assert facts.termination.issuer is TerminationIssuer.UNKNOWN


@pytest.mark.parametrize("code", [0, 1, 2, 126, 127, 128, 160, 255])
def test_an_ordinary_exit_code_is_an_agent_exit(code: int) -> None:
    facts = attempt_facts_for_failure(AgentInvocationError(AGENT, code))

    assert facts.termination is not None
    assert facts.termination.issuer is TerminationIssuer.AGENT_EXIT
