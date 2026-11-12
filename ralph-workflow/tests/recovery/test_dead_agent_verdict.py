from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.attribution import (
    Attribution,
    AttributionVerdict,
    RuntimeCause,
    attribute_attempt,
)
from ralph.recovery.canary_probe import ProbeOutcome
from ralph.recovery.dead_agent_verdict import (
    DeadAgentVerdict,
    PersistedVerdict,
    TypedSignal,
    authoritative_typed_signal_convicts_agent,
    decide_dead_agent_verdict,
    probe_outcome_verdict,
    runtime_issued_termination_is_not_agent_fault,
    task_or_environment_attribution_is_not_agent_fault,
    verdict_benches_agent,
    verdict_counts_toward_agent_state,
)

_UNKNOWN_ATTRIBUTION = AttributionVerdict(
    attribution=Attribution.UNKNOWN,
    cleared=False,
    counts_toward_agent_state=False,
    reason="no_rule_fired",
)

_AGENT_ATTRIBUTION = AttributionVerdict(
    attribution=Attribution.AGENT,
    cleared=False,
    counts_toward_agent_state=True,
    reason="authoritative_typed_signal",
)

_QUOTA_TYPED_SIGNAL = TypedSignal(
    name="QuotaExhaustedError",
    provider_originated=True,
    agent_initiated=True,
)


def _quota_error_step_incident() -> AttemptFacts:
    return AttemptFacts(
        event_kinds=("error_line",),
        tool_calls=(),
        workspace_changed_by_tool=False,
        artifact_valid=False,
        workspace_changed=False,
        elapsed_to_terminal_ms=4_000,
        exit_status="failed",
        liveness_observed=False,
        subagent_activity_observed=False,
        termination=TerminationRecord.create(
            TerminationIssuer.AGENT_EXIT,
            observed_returncode=0,
            reported_returncode=0,
            liveness_observed=False,
        ),
    )


def _codex_tool_result_quote_kill() -> AttemptFacts:
    return AttemptFacts(
        event_kinds=("tool_use", "tool_result"),
        tool_calls=("read_multiple_files",),
        workspace_changed_by_tool=False,
        artifact_valid=False,
        workspace_changed=False,
        elapsed_to_terminal_ms=15_000,
        exit_status="failed",
        liveness_observed=True,
        subagent_activity_observed=False,
        termination=TerminationRecord.create(
            TerminationIssuer.QUOTA_DETECTOR,
            observed_returncode=-15,
            reported_returncode=143,
            liveness_observed=True,
        ),
    )


def _opencode_subagent_kill() -> AttemptFacts:
    return AttemptFacts(
        event_kinds=("tool_use", "child_progress"),
        tool_calls=("task",),
        workspace_changed_by_tool=None,
        artifact_valid=False,
        workspace_changed=None,
        elapsed_to_terminal_ms=18_000,
        exit_status="failed",
        liveness_observed=True,
        subagent_activity_observed=True,
        termination=TerminationRecord.create(
            TerminationIssuer.UNKNOWN,
            observed_returncode=-15,
            reported_returncode=143,
            liveness_observed=True,
        ),
    )


def _slow_productive_mid_run_error() -> AttemptFacts:
    return AttemptFacts(
        event_kinds=("tool_use", "tool_result", "progress_report", "error_line"),
        tool_calls=("edit_file",),
        workspace_changed_by_tool=True,
        artifact_valid=True,
        workspace_changed=True,
        elapsed_to_terminal_ms=180_000,
        exit_status="failed",
        liveness_observed=True,
        subagent_activity_observed=False,
        termination=TerminationRecord.create(
            TerminationIssuer.AGENT_EXIT,
            observed_returncode=0,
            reported_returncode=0,
            liveness_observed=True,
        ),
    )


def _arbitrary_wording_exit_zero_no_work() -> AttemptFacts:
    return AttemptFacts(
        event_kinds=("error_line",),
        tool_calls=(),
        workspace_changed_by_tool=False,
        artifact_valid=False,
        workspace_changed=False,
        elapsed_to_terminal_ms=900,
        exit_status="the upstream refused the request for a reason nobody has seen before",
        liveness_observed=False,
        subagent_activity_observed=False,
        termination=TerminationRecord.create(
            TerminationIssuer.AGENT_EXIT,
            observed_returncode=0,
            reported_returncode=0,
            liveness_observed=False,
        ),
    )


def _silent_exit_zero_no_work() -> AttemptFacts:
    return AttemptFacts(
        event_kinds=(),
        tool_calls=(),
        workspace_changed_by_tool=False,
        artifact_valid=False,
        workspace_changed=False,
        elapsed_to_terminal_ms=200,
        exit_status="exit_0",
        liveness_observed=False,
        subagent_activity_observed=False,
        termination=TerminationRecord.create(
            TerminationIssuer.AGENT_EXIT,
            observed_returncode=0,
            reported_returncode=0,
            liveness_observed=False,
        ),
    )


def _outcome_already_exists() -> AttemptFacts:
    return AttemptFacts(
        event_kinds=("tool_use", "output_line"),
        tool_calls=("bash",),
        workspace_changed_by_tool=False,
        artifact_valid=False,
        workspace_changed=False,
        elapsed_to_terminal_ms=7_000,
        exit_status="exit_0",
        liveness_observed=True,
        subagent_activity_observed=False,
        termination=TerminationRecord.create(
            TerminationIssuer.AGENT_EXIT,
            observed_returncode=0,
            reported_returncode=0,
            liveness_observed=True,
        ),
    )


def _peers_fail_identically() -> AttemptFacts:
    return AttemptFacts(
        event_kinds=("tool_use", "tool_result"),
        tool_calls=("bash",),
        workspace_changed_by_tool=False,
        artifact_valid=False,
        workspace_changed=False,
        elapsed_to_terminal_ms=11_000,
        exit_status="exit_1",
        liveness_observed=True,
        subagent_activity_observed=False,
        termination=TerminationRecord.create(
            TerminationIssuer.AGENT_EXIT,
            observed_returncode=1,
            reported_returncode=1,
            liveness_observed=True,
        ),
    )


def test_dead_agent_verdict_exposes_exactly_the_four_literals() -> None:
    assert {verdict.value for verdict in DeadAgentVerdict} == {
        "agent_dead",
        "healthy",
        "not_agent_fault",
        "unknown",
    }


@pytest.mark.parametrize(
    ("member", "value"),
    (
        (DeadAgentVerdict.AGENT_DEAD, "agent_dead"),
        (DeadAgentVerdict.HEALTHY, "healthy"),
        (DeadAgentVerdict.NOT_AGENT_FAULT, "not_agent_fault"),
        (DeadAgentVerdict.UNKNOWN, "unknown"),
    ),
)
def test_every_verdict_member_carries_its_exact_string_value(
    member: DeadAgentVerdict,
    value: str,
) -> None:
    assert member.value == value
    assert str(member) == value
    assert DeadAgentVerdict(value) is member


def test_a_typed_signal_without_its_flags_convicts_nothing() -> None:
    signal = TypedSignal(name="QuotaExhaustedError")

    assert signal.provider_originated is False
    assert signal.agent_initiated is False
    assert authoritative_typed_signal_convicts_agent(signal) is False


def test_the_persisted_verdict_is_frozen() -> None:
    verdict = PersistedVerdict(
        verdict=DeadAgentVerdict.UNKNOWN,
        facts=AttemptFacts(),
        attribution=_UNKNOWN_ATTRIBUTION,
        probe_outcome=None,
        termination=None,
        typed_signal=None,
        return_code_discrepancy=False,
    )
    mutable: Any = verdict

    with pytest.raises(dataclasses.FrozenInstanceError):
        mutable.verdict = DeadAgentVerdict.AGENT_DEAD


@pytest.mark.parametrize(
    "issuer",
    (
        TerminationIssuer.QUOTA_DETECTOR,
        TerminationIssuer.SUPERVISOR_IDLE,
        TerminationIssuer.RECOVERY_TIMEOUT,
        TerminationIssuer.OPERATOR,
    ),
)
def test_a_runtime_issued_termination_is_never_the_agents_fault(
    issuer: TerminationIssuer,
) -> None:
    facts = AttemptFacts(
        liveness_observed=True,
        termination=TerminationRecord.create(issuer, liveness_observed=True),
    )

    assert runtime_issued_termination_is_not_agent_fault(facts) is True

    verdict = decide_dead_agent_verdict(
        facts,
        _UNKNOWN_ATTRIBUTION,
        ProbeOutcome.FAILED_REPRODUCING_NO_WORK,
    )

    assert verdict.verdict is DeadAgentVerdict.NOT_AGENT_FAULT
    assert verdict_benches_agent(verdict.verdict) is False
    assert verdict_counts_toward_agent_state(verdict.verdict) is False


@pytest.mark.parametrize(
    "issuer",
    (
        TerminationIssuer.AGENT_EXIT,
        TerminationIssuer.UNKNOWN,
    ),
)
def test_an_agent_or_unattributed_issuer_is_not_a_runtime_termination(
    issuer: TerminationIssuer,
) -> None:
    facts = AttemptFacts(termination=TerminationRecord.create(issuer))

    assert runtime_issued_termination_is_not_agent_fault(facts) is False


def test_a_session_without_a_termination_record_is_not_a_runtime_termination() -> None:
    assert runtime_issued_termination_is_not_agent_fault(AttemptFacts()) is False


def test_a_runtime_issued_termination_keeps_the_labelled_runtime_cause() -> None:
    facts = _codex_tool_result_quote_kill()
    attribution = attribute_attempt(facts)

    verdict = decide_dead_agent_verdict(
        facts,
        attribution,
        ProbeOutcome.FAILED_REPRODUCING_NO_WORK,
    )

    assert verdict.verdict is DeadAgentVerdict.NOT_AGENT_FAULT
    assert verdict.attribution.runtime_cause is not None
    assert verdict.attribution.runtime_cause.value == "runtime_kill_quota_misread"
    assert verdict.attribution.counts_toward_agent_state is False


def test_a_task_or_environment_attribution_is_not_the_agents_fault() -> None:
    attribution = AttributionVerdict(
        attribution=Attribution.TASK_OR_ENVIRONMENT,
        cleared=True,
        counts_toward_agent_state=False,
        reason="peers_fail_identically",
    )

    assert task_or_environment_attribution_is_not_agent_fault(attribution) is True

    verdict = decide_dead_agent_verdict(
        _peers_fail_identically(),
        attribution,
        ProbeOutcome.FAILED_REPRODUCING_NO_WORK,
    )

    assert verdict.verdict is DeadAgentVerdict.NOT_AGENT_FAULT
    assert verdict_benches_agent(verdict.verdict) is False
    assert verdict_counts_toward_agent_state(verdict.verdict) is False


def test_a_runtime_attribution_is_not_cleared_by_the_task_rule() -> None:
    attribution = AttributionVerdict(
        attribution=Attribution.RUNTIME,
        runtime_cause=RuntimeCause.KILL_UNATTRIBUTED,
        cleared=True,
        counts_toward_agent_state=False,
        reason="runtime_or_unattributed_kill_of_live_session",
    )

    assert task_or_environment_attribution_is_not_agent_fault(attribution) is False


def test_an_agent_attribution_is_not_cleared_by_the_task_rule() -> None:
    assert task_or_environment_attribution_is_not_agent_fault(_AGENT_ATTRIBUTION) is False


def test_an_authoritative_typed_signal_convicts_without_a_probe() -> None:
    facts = _quota_error_step_incident()
    attribution = attribute_attempt(
        facts,
        typed_signal_observed=True,
        typed_signal_provider_originated=True,
    )

    verdict = decide_dead_agent_verdict(facts, attribution, None, _QUOTA_TYPED_SIGNAL)

    assert verdict.verdict is DeadAgentVerdict.AGENT_DEAD
    assert verdict_benches_agent(verdict.verdict) is True
    assert verdict_counts_toward_agent_state(verdict.verdict) is True
    assert verdict.probe_outcome is None


def test_a_typed_signal_that_is_not_provider_originated_does_not_convict() -> None:
    signal = TypedSignal(
        name="QuotaExhaustedError",
        provider_originated=False,
        agent_initiated=True,
    )

    assert authoritative_typed_signal_convicts_agent(signal) is False

    verdict = decide_dead_agent_verdict(
        _arbitrary_wording_exit_zero_no_work(),
        _UNKNOWN_ATTRIBUTION,
        None,
        signal,
    )

    assert verdict.verdict is DeadAgentVerdict.UNKNOWN
    assert verdict_benches_agent(verdict.verdict) is False


def test_a_provider_originated_signal_that_is_not_agent_initiated_does_not_convict() -> None:
    signal = TypedSignal(
        name="QuotaExhaustedError",
        provider_originated=True,
        agent_initiated=False,
    )

    assert authoritative_typed_signal_convicts_agent(signal) is False

    verdict = decide_dead_agent_verdict(
        _arbitrary_wording_exit_zero_no_work(),
        _UNKNOWN_ATTRIBUTION,
        None,
        signal,
    )

    assert verdict.verdict is DeadAgentVerdict.UNKNOWN


def test_a_typed_signal_that_is_not_provider_originated_falls_through_to_the_probe() -> None:
    signal = TypedSignal(
        name="QuotaExhaustedError",
        provider_originated=False,
        agent_initiated=True,
    )

    verdict = decide_dead_agent_verdict(
        _arbitrary_wording_exit_zero_no_work(),
        _UNKNOWN_ATTRIBUTION,
        ProbeOutcome.FAILED_REPRODUCING_NO_WORK,
        signal,
    )

    assert verdict.verdict is DeadAgentVerdict.AGENT_DEAD


@pytest.mark.parametrize(
    ("probe_outcome", "expected"),
    (
        (ProbeOutcome.FAILED_REPRODUCING_NO_WORK, DeadAgentVerdict.AGENT_DEAD),
        (ProbeOutcome.PASSED, DeadAgentVerdict.HEALTHY),
        (ProbeOutcome.INCONCLUSIVE, DeadAgentVerdict.UNKNOWN),
        (None, DeadAgentVerdict.UNKNOWN),
    ),
)
def test_the_probe_decides_when_nothing_earlier_applies(
    probe_outcome: ProbeOutcome | None,
    expected: DeadAgentVerdict,
) -> None:
    assert probe_outcome_verdict(probe_outcome) is expected

    verdict = decide_dead_agent_verdict(
        _arbitrary_wording_exit_zero_no_work(),
        _UNKNOWN_ATTRIBUTION,
        probe_outcome,
    )

    assert verdict.verdict is expected


@pytest.mark.parametrize(
    ("facts", "attribution", "probe_outcome", "typed_signal", "expected"),
    (
        pytest.param(
            _quota_error_step_incident(),
            attribute_attempt(
                _quota_error_step_incident(),
                typed_signal_observed=True,
                typed_signal_provider_originated=True,
            ),
            None,
            _QUOTA_TYPED_SIGNAL,
            DeadAgentVerdict.AGENT_DEAD,
            id="quota_error_step_incident",
        ),
        pytest.param(
            _codex_tool_result_quote_kill(),
            attribute_attempt(_codex_tool_result_quote_kill()),
            None,
            None,
            DeadAgentVerdict.NOT_AGENT_FAULT,
            id="codex_tool_result_quote_kill",
        ),
        pytest.param(
            _opencode_subagent_kill(),
            attribute_attempt(_opencode_subagent_kill()),
            ProbeOutcome.PASSED,
            None,
            DeadAgentVerdict.HEALTHY,
            id="opencode_subagent_kill",
        ),
        pytest.param(
            _slow_productive_mid_run_error(),
            attribute_attempt(_slow_productive_mid_run_error()),
            ProbeOutcome.PASSED,
            None,
            DeadAgentVerdict.HEALTHY,
            id="slow_productive_mid_run_error",
        ),
    ),
)
def test_the_four_incident_shapes_map_to_their_expected_verdict(
    facts: AttemptFacts,
    attribution: AttributionVerdict,
    probe_outcome: ProbeOutcome | None,
    typed_signal: TypedSignal | None,
    expected: DeadAgentVerdict,
) -> None:
    verdict = decide_dead_agent_verdict(facts, attribution, probe_outcome, typed_signal)

    assert verdict.verdict is expected


def test_the_quota_error_step_shape_benches_on_its_first_verdict() -> None:
    facts = _quota_error_step_incident()
    attribution = attribute_attempt(
        facts,
        typed_signal_observed=True,
        typed_signal_provider_originated=True,
    )

    verdict = decide_dead_agent_verdict(facts, attribution, None, _QUOTA_TYPED_SIGNAL)

    assert verdict.verdict is DeadAgentVerdict.AGENT_DEAD
    assert verdict_benches_agent(verdict.verdict) is True


def test_a_probe_failure_convicts_an_arbitrary_wording_exit_zero_attempt() -> None:
    facts = _arbitrary_wording_exit_zero_no_work()

    verdict = decide_dead_agent_verdict(
        facts,
        attribute_attempt(facts),
        ProbeOutcome.FAILED_REPRODUCING_NO_WORK,
    )

    assert verdict.verdict is DeadAgentVerdict.AGENT_DEAD
    assert verdict_benches_agent(verdict.verdict) is True
    assert verdict_counts_toward_agent_state(verdict.verdict) is True


def test_a_passing_probe_clears_a_silent_exit_zero_attempt() -> None:
    facts = _silent_exit_zero_no_work()

    verdict = decide_dead_agent_verdict(
        facts,
        attribute_attempt(facts),
        ProbeOutcome.PASSED,
    )

    assert verdict.verdict is DeadAgentVerdict.HEALTHY
    assert verdict_benches_agent(verdict.verdict) is False


def test_an_absent_probe_leaves_the_verdict_unknown() -> None:
    facts = _silent_exit_zero_no_work()

    verdict = decide_dead_agent_verdict(facts, attribute_attempt(facts), None)

    assert verdict.verdict is DeadAgentVerdict.UNKNOWN
    assert verdict_benches_agent(verdict.verdict) is False
    assert verdict_counts_toward_agent_state(verdict.verdict) is True


def test_an_inconclusive_probe_leaves_the_verdict_unknown() -> None:
    facts = _silent_exit_zero_no_work()

    verdict = decide_dead_agent_verdict(
        facts,
        attribute_attempt(facts),
        ProbeOutcome.INCONCLUSIVE,
    )

    assert verdict.verdict is DeadAgentVerdict.UNKNOWN
    assert verdict_benches_agent(verdict.verdict) is False


def test_a_satisfied_outcome_is_not_the_agents_fault() -> None:
    facts = _outcome_already_exists()
    attribution = AttributionVerdict(
        attribution=Attribution.TASK_OR_ENVIRONMENT,
        cleared=True,
        counts_toward_agent_state=False,
        reason="satisfied_outcome",
    )

    verdict = decide_dead_agent_verdict(facts, attribution, None)

    assert verdict.verdict is DeadAgentVerdict.NOT_AGENT_FAULT
    assert verdict_benches_agent(verdict.verdict) is False
    assert verdict_counts_toward_agent_state(verdict.verdict) is False


def test_peers_failing_identically_is_not_the_agents_fault() -> None:
    facts = _peers_fail_identically()
    attribution = attribute_attempt(facts, peer_failures_identical=True)

    assert attribution.attribution is Attribution.TASK_OR_ENVIRONMENT

    verdict = decide_dead_agent_verdict(facts, attribution, None)

    assert verdict.verdict is DeadAgentVerdict.NOT_AGENT_FAULT
    assert verdict_benches_agent(verdict.verdict) is False
    assert verdict_counts_toward_agent_state(verdict.verdict) is False


def test_a_runtime_termination_with_a_failed_probe_is_never_benched() -> None:
    facts = _codex_tool_result_quote_kill()
    attribution = attribute_attempt(facts)

    verdict = decide_dead_agent_verdict(
        facts,
        attribution,
        ProbeOutcome.FAILED_REPRODUCING_NO_WORK,
    )

    assert verdict.verdict is DeadAgentVerdict.NOT_AGENT_FAULT
    assert verdict_benches_agent(verdict.verdict) is False


def test_an_unattributed_kill_with_a_passing_probe_is_healthy() -> None:
    facts = _opencode_subagent_kill()
    attribution = attribute_attempt(facts)

    verdict = decide_dead_agent_verdict(facts, attribution, ProbeOutcome.PASSED)

    assert verdict.verdict is DeadAgentVerdict.HEALTHY
    assert verdict_benches_agent(verdict.verdict) is False


def test_an_unattributed_kill_with_a_failing_probe_convicts_the_agent() -> None:
    facts = _opencode_subagent_kill()
    attribution = attribute_attempt(facts)

    verdict = decide_dead_agent_verdict(facts, attribution, ProbeOutcome.FAILED_REPRODUCING_NO_WORK)

    assert verdict.verdict is DeadAgentVerdict.AGENT_DEAD
    assert verdict_benches_agent(verdict.verdict) is True


@pytest.mark.parametrize(
    ("verdict", "benches", "counts"),
    (
        (DeadAgentVerdict.AGENT_DEAD, True, True),
        (DeadAgentVerdict.HEALTHY, False, True),
        (DeadAgentVerdict.NOT_AGENT_FAULT, False, False),
        (DeadAgentVerdict.UNKNOWN, False, True),
    ),
)
def test_only_a_dead_agent_benches_and_only_a_non_agent_fault_is_excluded(
    verdict: DeadAgentVerdict,
    benches: bool,
    counts: bool,
) -> None:
    assert verdict_benches_agent(verdict) is benches
    assert verdict_counts_toward_agent_state(verdict) is counts


def test_the_persisted_verdict_carries_every_layer_output() -> None:
    facts = _codex_tool_result_quote_kill()
    attribution = attribute_attempt(facts)
    signal = TypedSignal(
        name="QuotaExhaustedError",
        provider_originated=True,
        agent_initiated=True,
    )

    verdict = decide_dead_agent_verdict(facts, attribution, ProbeOutcome.PASSED, signal)

    assert verdict.facts is facts
    assert verdict.attribution is attribution
    assert verdict.probe_outcome is ProbeOutcome.PASSED
    assert verdict.termination is facts.termination
    assert verdict.typed_signal is signal


def test_the_persisted_verdict_flags_disagreeing_return_codes() -> None:
    facts = _codex_tool_result_quote_kill()

    verdict = decide_dead_agent_verdict(facts, attribute_attempt(facts), None)

    assert verdict.return_code_discrepancy is True
    assert verdict.termination is not None
    assert verdict.termination.observed_returncode == -15
    assert verdict.termination.reported_returncode == 143


def test_the_persisted_verdict_reports_no_discrepancy_for_agreeing_codes() -> None:
    facts = _quota_error_step_incident()

    verdict = decide_dead_agent_verdict(facts, attribute_attempt(facts), None)

    assert verdict.return_code_discrepancy is False


def test_the_persisted_verdict_of_a_session_without_a_termination_is_empty() -> None:
    facts = AttemptFacts()

    verdict = decide_dead_agent_verdict(facts, _UNKNOWN_ATTRIBUTION, None)

    assert verdict.termination is None
    assert verdict.return_code_discrepancy is False
