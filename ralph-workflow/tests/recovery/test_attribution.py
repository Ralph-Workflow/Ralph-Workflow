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
    authoritative_typed_signal_convicts_agent,
    mid_run_activity_clears_agent,
    peers_fail_identically_points_to_task,
    runtime_or_unattributed_kill_of_live_session_clears_agent,
    satisfied_outcome_clears_agent,
    workspace_writes_clear_agent,
)


def _agy_error_step_exit_zero_no_work() -> AttemptFacts:
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
            reported_returncode=0,
            liveness_observed=True,
        ),
    )


def _opencode_subagent_kill_without_issuer() -> AttemptFacts:
    return AttemptFacts(
        event_kinds=("tool_use", "child_progress"),
        tool_calls=("task",),
        workspace_changed_by_tool=False,
        artifact_valid=False,
        workspace_changed=False,
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
        artifact_valid=False,
        workspace_changed=True,
        elapsed_to_terminal_ms=120_000,
        exit_status="failed",
        liveness_observed=True,
        subagent_activity_observed=False,
        termination=TerminationRecord.create(
            TerminationIssuer.AGENT_EXIT,
            observed_returncode=1,
            reported_returncode=1,
            liveness_observed=True,
        ),
    )


def test_attribution_exposes_exactly_the_four_attribution_values() -> None:
    assert {item.value for item in Attribution} == {
        "agent",
        "runtime",
        "task_or_environment",
        "unknown",
    }


def test_runtime_cause_exposes_the_required_labelled_causes() -> None:
    assert RuntimeCause.KILL_QUOTA_MISREAD.value == "runtime_kill_quota_misread"
    assert RuntimeCause.KILL_UNATTRIBUTED.value == "runtime_kill_unattributed"
    assert {cause.value for cause in RuntimeCause} >= {
        "runtime_kill_quota_misread",
        "runtime_kill_unattributed",
    }


def test_attribution_verdict_is_frozen() -> None:
    verdict = AttributionVerdict(attribution=Attribution.UNKNOWN, reason="no_rule_fired")
    mutable: Any = verdict

    with pytest.raises(dataclasses.FrozenInstanceError):
        mutable.attribution = Attribution.AGENT


def test_runtime_attribution_requires_a_labelled_runtime_cause() -> None:
    with pytest.raises(ValueError):
        AttributionVerdict(attribution=Attribution.RUNTIME, reason="runtime_kill")


def test_a_runtime_cause_requires_a_runtime_attribution() -> None:
    with pytest.raises(ValueError):
        AttributionVerdict(
            attribution=Attribution.UNKNOWN,
            runtime_cause=RuntimeCause.KILL_UNATTRIBUTED,
            reason="runtime_kill",
        )


def test_a_cleared_verdict_cannot_attribute_the_agent() -> None:
    with pytest.raises(ValueError):
        AttributionVerdict(
            attribution=Attribution.AGENT,
            cleared=True,
            reason="authoritative_typed_signal",
        )


def test_runtime_and_task_attributions_cannot_count_toward_agent_state() -> None:
    with pytest.raises(ValueError):
        AttributionVerdict(
            attribution=Attribution.TASK_OR_ENVIRONMENT,
            counts_toward_agent_state=True,
            reason="satisfied_outcome",
        )
    with pytest.raises(ValueError):
        AttributionVerdict(
            attribution=Attribution.RUNTIME,
            runtime_cause=RuntimeCause.KILL_UNATTRIBUTED,
            counts_toward_agent_state=True,
            reason="runtime_or_unattributed_kill_of_live_session",
        )


@pytest.mark.parametrize(
    ("issuer", "expected_cause"),
    (
        (TerminationIssuer.QUOTA_DETECTOR, RuntimeCause.KILL_QUOTA_MISREAD),
        (TerminationIssuer.SUPERVISOR_IDLE, RuntimeCause.KILL_SUPERVISOR_IDLE),
        (TerminationIssuer.RECOVERY_TIMEOUT, RuntimeCause.KILL_RECOVERY_TIMEOUT),
        (TerminationIssuer.OPERATOR, RuntimeCause.KILL_OPERATOR),
        (TerminationIssuer.UNKNOWN, RuntimeCause.KILL_UNATTRIBUTED),
    ),
)
def test_runtime_kill_of_live_session_clears_the_agent_with_a_labelled_cause(
    issuer: TerminationIssuer,
    expected_cause: RuntimeCause,
) -> None:
    facts = AttemptFacts(
        liveness_observed=True,
        termination=TerminationRecord.create(issuer, liveness_observed=True),
    )

    verdict = runtime_or_unattributed_kill_of_live_session_clears_agent(facts)

    assert verdict is not None
    assert verdict.attribution is Attribution.RUNTIME
    assert verdict.runtime_cause is expected_cause
    assert verdict.cleared is True
    assert verdict.counts_toward_agent_state is False
    assert verdict.reason == "runtime_or_unattributed_kill_of_live_session"


def test_runtime_kill_without_observed_liveness_does_not_clear_the_agent() -> None:
    facts = AttemptFacts(
        liveness_observed=False,
        termination=TerminationRecord.create(
            TerminationIssuer.QUOTA_DETECTOR,
            observed_returncode=-15,
            liveness_observed=False,
        ),
    )

    assert runtime_or_unattributed_kill_of_live_session_clears_agent(facts) is None


def test_agent_exit_is_not_a_runtime_kill() -> None:
    facts = AttemptFacts(
        liveness_observed=True,
        termination=TerminationRecord.create(
            TerminationIssuer.AGENT_EXIT,
            observed_returncode=1,
            liveness_observed=True,
        ),
    )

    assert runtime_or_unattributed_kill_of_live_session_clears_agent(facts) is None


def test_satisfied_outcome_clears_the_agent() -> None:
    facts = AttemptFacts(artifact_valid=True)

    verdict = satisfied_outcome_clears_agent(facts)

    assert verdict is not None
    assert verdict.attribution is Attribution.TASK_OR_ENVIRONMENT
    assert verdict.runtime_cause is None
    assert verdict.cleared is True
    assert verdict.counts_toward_agent_state is False
    assert verdict.reason == "satisfied_outcome"


def test_an_unfulfilled_outcome_is_not_cleared_by_the_satisfied_rule() -> None:
    assert satisfied_outcome_clears_agent(AttemptFacts(artifact_valid=False)) is None


def test_peers_failing_identically_points_to_the_task() -> None:
    facts = AttemptFacts()

    verdict = peers_fail_identically_points_to_task(facts, peer_failures_identical=True)

    assert verdict is not None
    assert verdict.attribution is Attribution.TASK_OR_ENVIRONMENT
    assert verdict.runtime_cause is None
    assert verdict.cleared is True
    assert verdict.counts_toward_agent_state is False
    assert verdict.reason == "peers_fail_identically"


def test_peers_failing_identically_needs_the_caller_supplied_signal() -> None:
    assert peers_fail_identically_points_to_task(AttemptFacts()) is None
    assert (
        peers_fail_identically_points_to_task(AttemptFacts(), peer_failures_identical=False) is None
    )


def test_a_satisfied_outcome_is_never_attributed_to_the_task_by_the_peer_rule() -> None:
    assert (
        peers_fail_identically_points_to_task(
            AttemptFacts(artifact_valid=True),
            peer_failures_identical=True,
        )
        is None
    )


def test_workspace_writes_clear_the_agent() -> None:
    facts = AttemptFacts(workspace_changed=True, workspace_changed_by_tool=True)

    verdict = workspace_writes_clear_agent(facts)

    assert verdict is not None
    assert verdict.attribution is Attribution.TASK_OR_ENVIRONMENT
    assert verdict.runtime_cause is None
    assert verdict.cleared is True
    assert verdict.counts_toward_agent_state is False
    assert verdict.reason == "workspace_writes"


def test_an_unchanged_workspace_is_not_cleared_by_the_workspace_rule() -> None:
    facts = AttemptFacts(workspace_changed=False, workspace_changed_by_tool=False)

    assert workspace_writes_clear_agent(facts) is None


def test_mid_run_activity_clears_the_agent() -> None:
    facts = AttemptFacts(event_kinds=("tool_use", "progress_report", "error_line"))

    verdict = mid_run_activity_clears_agent(facts)

    assert verdict is not None
    assert verdict.attribution is Attribution.TASK_OR_ENVIRONMENT
    assert verdict.runtime_cause is None
    assert verdict.cleared is True
    assert verdict.counts_toward_agent_state is False
    assert verdict.reason == "mid_run_activity"


def test_a_session_without_mid_run_activity_is_not_cleared() -> None:
    facts = AttemptFacts(event_kinds=("output_line", "error_line"))

    assert mid_run_activity_clears_agent(facts) is None


def test_authoritative_typed_signal_convicts_the_agent() -> None:
    facts = AttemptFacts(event_kinds=("error_line",))

    verdict = authoritative_typed_signal_convicts_agent(
        facts,
        typed_signal_observed=True,
        typed_signal_provider_originated=True,
    )

    assert verdict is not None
    assert verdict.attribution is Attribution.AGENT
    assert verdict.runtime_cause is None
    assert verdict.cleared is False
    assert verdict.counts_toward_agent_state is True
    assert verdict.reason == "authoritative_typed_signal"


def test_a_typed_signal_without_provider_origin_never_convicts() -> None:
    facts = AttemptFacts(event_kinds=("error_line",))

    assert (
        authoritative_typed_signal_convicts_agent(
            facts,
            typed_signal_observed=True,
            typed_signal_provider_originated=False,
        )
        is None
    )


def test_a_provider_originated_signal_without_a_typed_signal_never_convicts() -> None:
    facts = AttemptFacts(event_kinds=("error_line",))

    assert (
        authoritative_typed_signal_convicts_agent(
            facts,
            typed_signal_observed=False,
            typed_signal_provider_originated=True,
        )
        is None
    )


@pytest.mark.parametrize(
    ("facts", "expected_attribution", "expected_cause", "expected_cleared", "kwargs"),
    (
        pytest.param(
            _agy_error_step_exit_zero_no_work(),
            Attribution.AGENT,
            None,
            False,
            {
                "typed_signal_observed": True,
                "typed_signal_provider_originated": True,
            },
            id="agy_error_step_exit_zero_no_work",
        ),
        pytest.param(
            _codex_tool_result_quote_kill(),
            Attribution.RUNTIME,
            RuntimeCause.KILL_QUOTA_MISREAD,
            True,
            {},
            id="codex_tool_result_quote_kill",
        ),
        pytest.param(
            _opencode_subagent_kill_without_issuer(),
            Attribution.RUNTIME,
            RuntimeCause.KILL_UNATTRIBUTED,
            True,
            {},
            id="opencode_subagent_kill_without_issuer",
        ),
        pytest.param(
            _slow_productive_mid_run_error(),
            Attribution.TASK_OR_ENVIRONMENT,
            None,
            True,
            {},
            id="slow_productive_mid_run_error",
        ),
    ),
)
def test_incident_fact_shapes_map_to_the_expected_attribution(
    facts: AttemptFacts,
    expected_attribution: Attribution,
    expected_cause: RuntimeCause | None,
    expected_cleared: bool,
    kwargs: dict[str, bool],
) -> None:
    verdict = attribute_attempt(facts, **kwargs)

    assert verdict.attribution is expected_attribution
    assert verdict.runtime_cause is expected_cause
    assert verdict.cleared is expected_cleared


def test_runtime_and_task_attributions_never_count_toward_agent_state() -> None:
    runtime_verdict = attribute_attempt(_codex_tool_result_quote_kill())
    task_verdict = attribute_attempt(_slow_productive_mid_run_error())

    assert runtime_verdict.attribution is Attribution.RUNTIME
    assert runtime_verdict.counts_toward_agent_state is False
    assert task_verdict.attribution is Attribution.TASK_OR_ENVIRONMENT
    assert task_verdict.counts_toward_agent_state is False


@pytest.mark.parametrize(
    "facts",
    (
        pytest.param(
            AttemptFacts(
                event_kinds=("error_line",),
                exit_status="provider rejected the request for an unknown reason",
                termination=TerminationRecord.create(
                    TerminationIssuer.AGENT_EXIT,
                    observed_returncode=0,
                    reported_returncode=0,
                ),
            ),
            id="arbitrary_error_wording_exit_zero",
        ),
        pytest.param(
            AttemptFacts(
                event_kinds=("output_line",),
                exit_status="failed",
                termination=TerminationRecord.create(
                    TerminationIssuer.AGENT_EXIT,
                    observed_returncode=-15,
                    reported_returncode=-15,
                ),
            ),
            id="exit_minus_fifteen_without_liveness",
        ),
        pytest.param(
            AttemptFacts(
                event_kinds=("error_line", "output_line"),
                exit_status="failed",
                termination=TerminationRecord.create(
                    TerminationIssuer.UNKNOWN,
                    observed_returncode=-15,
                    reported_returncode=143,
                ),
            ),
            id="unattributed_kill_without_liveness",
        ),
        pytest.param(
            AttemptFacts(
                event_kinds=("output_line", "error_line"),
                exit_status="failed",
                workspace_changed=False,
            ),
            id="event_shapes_only",
        ),
    ),
)
def test_passive_evidence_alone_never_attributes_the_agent(facts: AttemptFacts) -> None:
    verdict = attribute_attempt(facts)

    assert verdict.attribution is not Attribution.AGENT
    assert verdict.attribution is Attribution.UNKNOWN
    assert verdict.cleared is False
    assert verdict.counts_toward_agent_state is False


def test_attribute_attempt_defaults_to_unknown_when_no_rule_fires() -> None:
    verdict = attribute_attempt(AttemptFacts())

    assert verdict.attribution is Attribution.UNKNOWN
    assert verdict.runtime_cause is None
    assert verdict.cleared is False
    assert verdict.counts_toward_agent_state is False
    assert verdict.reason == "no_rule_fired"


def test_a_clearing_rule_wins_over_the_typed_signal() -> None:
    verdict = attribute_attempt(
        _codex_tool_result_quote_kill(),
        typed_signal_observed=True,
        typed_signal_provider_originated=True,
    )

    assert verdict.attribution is Attribution.RUNTIME
    assert verdict.runtime_cause is RuntimeCause.KILL_QUOTA_MISREAD
    assert verdict.cleared is True
    assert verdict.counts_toward_agent_state is False


def test_the_satisfied_outcome_wins_over_a_typed_signal() -> None:
    verdict = attribute_attempt(
        AttemptFacts(artifact_valid=True),
        typed_signal_observed=True,
        typed_signal_provider_originated=True,
    )

    assert verdict.attribution is Attribution.TASK_OR_ENVIRONMENT
    assert verdict.cleared is True


def test_the_peer_signal_reaches_the_orchestrator() -> None:
    verdict = attribute_attempt(AttemptFacts(), peer_failures_identical=True)

    assert verdict.attribution is Attribution.TASK_OR_ENVIRONMENT
    assert verdict.cleared is True
    assert verdict.reason == "peers_fail_identically"
