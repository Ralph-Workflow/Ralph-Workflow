from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from ralph.recovery.attempt_facts import (
    AttemptFacts,
    TerminationIssuer,
    TerminationRecord,
    WorkspaceChangeState,
    has_return_code_discrepancy,
    workspace_changed_state,
)


def test_termination_issuer_exposes_the_required_vocabulary() -> None:
    assert {issuer.value for issuer in TerminationIssuer} == {
        "agent_exit",
        "supervisor_idle",
        "quota_detector",
        "recovery_timeout",
        "post_exit_watchdog",
        "operator",
        "unknown",
    }


@pytest.mark.parametrize(
    ("issuer", "value"),
    (
        (TerminationIssuer.AGENT_EXIT, "agent_exit"),
        (TerminationIssuer.SUPERVISOR_IDLE, "supervisor_idle"),
        (TerminationIssuer.QUOTA_DETECTOR, "quota_detector"),
        (TerminationIssuer.RECOVERY_TIMEOUT, "recovery_timeout"),
        (TerminationIssuer.POST_EXIT_WATCHDOG, "post_exit_watchdog"),
        (TerminationIssuer.OPERATOR, "operator"),
        (TerminationIssuer.UNKNOWN, "unknown"),
    ),
)
def test_termination_issuer_member_carries_its_exact_string_value(
    issuer: TerminationIssuer,
    value: str,
) -> None:
    assert issuer.value == value
    assert str(issuer) == value
    assert TerminationIssuer(value) is issuer


@pytest.mark.parametrize(
    ("observed", "reported", "expected"),
    (
        (0, 0, False),
        (1, 1, False),
        (0, 1, True),
        (137, 0, True),
        (None, None, False),
        (None, 1, True),
        (1, None, True),
    ),
)
def test_return_code_discrepancy_flags_disagreeing_codes(
    observed: int | None,
    reported: int | None,
    expected: bool,
) -> None:
    record = TerminationRecord.create(
        TerminationIssuer.AGENT_EXIT,
        observed_returncode=observed,
        reported_returncode=reported,
    )

    assert record.return_code_discrepancy is expected
    assert has_return_code_discrepancy(record) is expected


def test_return_code_discrepancy_cannot_be_supplied_by_the_caller() -> None:
    with pytest.raises(TypeError):
        TerminationRecord.create(
            TerminationIssuer.AGENT_EXIT,
            observed_returncode=0,
            reported_returncode=0,
            return_code_discrepancy=True,
        )


def test_absent_termination_reports_no_discrepancy() -> None:
    assert has_return_code_discrepancy(None) is False


def test_direct_construction_derives_the_same_discrepancy_as_the_factory() -> None:
    record = TerminationRecord(
        issuer=TerminationIssuer.SUPERVISOR_IDLE,
        observed_returncode=3,
        reported_returncode=4,
        liveness_observed=True,
    )

    assert record.return_code_discrepancy is True
    assert record.liveness_observed is True
    assert has_return_code_discrepancy(record) is True


def test_attempt_facts_defaults_keep_every_observation_unknown() -> None:
    facts = AttemptFacts()

    assert facts.event_kinds == ()
    assert facts.tool_calls == ()
    assert facts.workspace_changed_by_tool is None
    assert facts.artifact_valid is False
    assert facts.workspace_changed is None
    assert facts.elapsed_to_terminal_ms is None
    assert facts.exit_status is None
    assert facts.liveness_observed is None
    assert facts.subagent_activity_observed is None
    assert facts.termination is None


@pytest.mark.parametrize(
    ("workspace_changed", "expected"),
    (
        (None, WorkspaceChangeState.UNKNOWN),
        (True, WorkspaceChangeState.TRUE),
        (False, WorkspaceChangeState.FALSE),
    ),
)
def test_workspace_changed_state_never_collapses_the_tri_state(
    workspace_changed: bool | None,
    expected: WorkspaceChangeState,
) -> None:
    facts = AttemptFacts(workspace_changed=workspace_changed)

    assert workspace_changed_state(facts) is expected
    assert workspace_changed_state(facts).value == expected.value


def test_workspace_change_state_values_are_exact() -> None:
    assert {state.value for state in WorkspaceChangeState} == {"true", "false", "unknown"}


def test_attempt_facts_is_frozen() -> None:
    facts = AttemptFacts()
    mutable_facts: Any = facts

    with pytest.raises(dataclasses.FrozenInstanceError):
        mutable_facts.workspace_changed = True


def test_termination_record_is_frozen() -> None:
    record = TerminationRecord.create(TerminationIssuer.QUOTA_DETECTOR)
    mutable_record: Any = record

    with pytest.raises(dataclasses.FrozenInstanceError):
        mutable_record.issuer = TerminationIssuer.OPERATOR


def test_attempt_facts_records_the_observed_attempt_shape() -> None:
    facts = AttemptFacts(
        event_kinds=("tool_call", "idle_timeout", "exit"),
        tool_calls=("read", "write"),
        workspace_changed_by_tool=True,
        artifact_valid=True,
        workspace_changed=True,
        elapsed_to_terminal_ms=1234,
        exit_status="failed",
        liveness_observed=False,
        subagent_activity_observed=None,
    )

    assert facts.event_kinds == ("tool_call", "idle_timeout", "exit")
    assert facts.tool_calls == ("read", "write")
    assert facts.workspace_changed_by_tool is True
    assert facts.artifact_valid is True
    assert facts.workspace_changed is True
    assert facts.elapsed_to_terminal_ms == 1234
    assert facts.exit_status == "failed"
    assert facts.liveness_observed is False
    assert facts.subagent_activity_observed is None


def test_attempt_facts_carries_the_termination_discrepancy() -> None:
    facts = AttemptFacts(
        termination=TerminationRecord.create(
            TerminationIssuer.RECOVERY_TIMEOUT,
            observed_returncode=0,
            reported_returncode=-15,
            liveness_observed=True,
        ),
    )

    assert facts.termination is not None
    assert facts.termination.issuer is TerminationIssuer.RECOVERY_TIMEOUT
    assert has_return_code_discrepancy(facts.termination) is True
