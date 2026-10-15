"""Tests for the seven distinct operator display labels and IntegrationAction derivations."""

from __future__ import annotations

from typing import NamedTuple

import pytest
from rich.console import Console

from ralph.display.auto_integrate_message import (
    IntegrationDisplayLabel,
    OperationDisplayState,
    format_auto_integrate_message,
    format_integration_label,
    integration_action_to_operation_display_state,
)
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.phase_status import (
    format_integration_phase_label,
)
from ralph.display.status_bar import (
    StatusBarModel,
    _abbreviated_phase_label,
    render_status_bar,
)
from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline.integration_resolution_status import IntegrationResolutionStatus


class StateTestCase(NamedTuple):
    state_key: str
    expected_label: str
    action: IntegrationAction | None
    display_state: OperationDisplayState
    expected_abbrev: str
    is_diagnosis: bool = False


SEVEN_STATES: list[StateTestCase] = [
    StateTestCase(
        state_key="conflict_resolution",
        expected_label="conflict resolution",
        action=IntegrationAction.RESOLVE_ACTIVE_OPERATION,
        display_state=OperationDisplayState.CONFLICT_RESOLUTION,
        expected_abbrev="Conf",
        is_diagnosis=False,
    ),
    StateTestCase(
        state_key="evidence_diagnosis",
        expected_label="evidence diagnosis",
        action=IntegrationAction.RESOLVE_ACTIVE_OPERATION,
        display_state=OperationDisplayState.EVIDENCE_DIAGNOSIS,
        expected_abbrev="Diag",
        is_diagnosis=True,
    ),
    StateTestCase(
        state_key="integration",
        expected_label="integration",
        action=IntegrationAction.CONTINUE_GIT_OPERATION,
        display_state=OperationDisplayState.INTEGRATING,
        expected_abbrev="Intg",
        is_diagnosis=False,
    ),
    StateTestCase(
        state_key="landing",
        expected_label="landing",
        action=IntegrationAction.LAND_TARGET,
        display_state=OperationDisplayState.LANDING,
        expected_abbrev="Land",
        is_diagnosis=False,
    ),
    StateTestCase(
        state_key="publication",
        expected_label="publication",
        action=IntegrationAction.PUBLISH_AFTER_LANDING,
        display_state=OperationDisplayState.PUBLISHING,
        expected_abbrev="Pub",
        is_diagnosis=False,
    ),
    StateTestCase(
        state_key="waiting",
        expected_label="waiting",
        action=IntegrationAction.WAIT_FOR_BOUNDED_RETRY,
        display_state=OperationDisplayState.WAITING,
        expected_abbrev="Wait",
        is_diagnosis=False,
    ),
    StateTestCase(
        state_key="intervention",
        expected_label="intervention",
        action=IntegrationAction.REQUIRE_INTERVENTION,
        display_state=OperationDisplayState.INTERVENTION_REQUIRED,
        expected_abbrev="Intv",
        is_diagnosis=False,
    ),
]


def _make_context(width: int = 120) -> DisplayContext:
    console = Console(width=width, force_terminal=True, color_system="truecolor")
    return make_display_context(console=console, env={})


def _case_id(c: StateTestCase) -> str:
    return c.state_key


@pytest.mark.parametrize("case", SEVEN_STATES, ids=_case_id)
def test_format_integration_label_from_display_state(case: StateTestCase) -> None:
    """Each of the seven states produces its canonical distinct label from display state."""
    label = format_integration_label(case.display_state, is_diagnosis=case.is_diagnosis)
    assert label == case.expected_label
    assert label == case.expected_label.lower()


@pytest.mark.parametrize("case", SEVEN_STATES, ids=_case_id)
def test_format_integration_label_from_action(case: StateTestCase) -> None:
    """Each of the seven states derives its canonical distinct label from IntegrationAction."""
    if case.action is not None:
        label = format_integration_label(case.action, is_diagnosis=case.is_diagnosis)
        assert label == case.expected_label


@pytest.mark.parametrize("case", SEVEN_STATES, ids=_case_id)
def test_format_integration_phase_label(case: StateTestCase) -> None:
    """Phase label formatting titles the label and appends optional detail."""
    base_title = format_integration_phase_label(case.display_state, is_diagnosis=case.is_diagnosis)
    assert base_title == case.expected_label.title()

    detailed = format_integration_phase_label(
        case.display_state, is_diagnosis=case.is_diagnosis, detail="round 1/3"
    )
    assert detailed == f"{case.expected_label.title()} (round 1/3)"


@pytest.mark.parametrize("case", SEVEN_STATES, ids=_case_id)
def test_status_bar_phase_abbreviation_per_state(case: StateTestCase) -> None:
    """_abbreviated_phase_label renders distinct abbreviation for each of the seven states."""
    abbrev = _abbreviated_phase_label(case.expected_label, budget=4, width=60)
    assert abbrev == case.expected_abbrev


@pytest.mark.parametrize("case", SEVEN_STATES, ids=_case_id)
def test_status_bar_distinct_attention_per_state(case: StateTestCase) -> None:
    """Status bar resolves attention presentation from operation_display_state distinctly."""
    ctx = _make_context(width=100)
    model = StatusBarModel(
        workspace_root="/home/user/repo",
        phase_label="Active Phase",
        phase_style="theme.phase.development",
        operation_display_state=case.display_state.value,
    )
    rendered = render_status_bar(model, ctx, home="/home/user")
    plain = rendered.plain

    # Ensure evidence diagnosis and conflict resolution are mutually exclusive
    if case.state_key == "evidence_diagnosis":
        assert "EVIDENCE_DIAGNOSIS" in plain
        assert "CONFLICT_RESOLUTION" not in plain
        assert "conflict" not in plain.lower()
    elif case.state_key == "conflict_resolution":
        assert "CONFLICT_RESOLUTION" in plain
        assert "EVIDENCE_DIAGNOSIS" not in plain
        assert "evidence" not in plain.lower()


def test_evidence_diagnosis_never_shown_as_conflict_resolution() -> None:
    """Evidence diagnosis is never mislabeled or displayed as conflict resolution."""
    # From action with diagnosis
    action_label = format_integration_label(
        IntegrationAction.RESOLVE_ACTIVE_OPERATION, is_diagnosis=True
    )
    assert action_label == "evidence diagnosis"
    assert "conflict" not in action_label

    # From display state
    state_label = format_integration_label(OperationDisplayState.EVIDENCE_DIAGNOSIS)
    assert state_label == "evidence diagnosis"
    assert "conflict" not in state_label

    # From object with retained recovery record
    class FakeRecoveryRecord:
        recovery_record_retained = True
        next_action = IntegrationAction.RESOLVE_ACTIVE_OPERATION

    record_label = format_integration_label(FakeRecoveryRecord())
    assert record_label == "evidence diagnosis"
    assert "conflict" not in record_label

    # From object with diagnostic ownership
    class FakeDiagnosticRecord:
        diagnostic_ownership = "hash123"
        next_action = IntegrationAction.RESOLVE_ACTIVE_OPERATION

    diag_label = format_integration_label(FakeDiagnosticRecord())
    assert diag_label == "evidence diagnosis"
    assert "conflict" not in diag_label


def test_format_auto_integrate_message_with_integration_action() -> None:
    """format_auto_integrate_message derives directly from integration_action."""
    msg = format_auto_integrate_message(
        integration_action=IntegrationAction.CONTINUE_GIT_OPERATION,
        target="main",
    )
    assert msg.startswith("integrating onto target (main)")

    msg_land = format_auto_integrate_message(
        integration_action=IntegrationAction.LAND_TARGET,
        target="main",
        fast_forwarded=True,
    )
    assert msg_land.startswith("landing (main), fast-forwarded main")

    msg_conflict = format_auto_integrate_message(
        integration_action=IntegrationAction.RESOLVE_ACTIVE_OPERATION,
        target="main",
    )
    assert msg_conflict.startswith("conflict resolution (main)")


def test_integration_resolution_status_display_labels() -> None:
    """IntegrationResolutionStatus projects to the canonical distinct labels."""
    assert IntegrationResolutionStatus.RESOLVED.to_display_label() == "landing"
    assert IntegrationResolutionStatus.RECOVERABLE.to_display_label() == "waiting"
    assert IntegrationResolutionStatus.EXHAUSTED.to_display_label() == "intervention"

    # Also via format_integration_label
    assert format_integration_label(IntegrationResolutionStatus.RESOLVED) == "landing"
    assert format_integration_label(IntegrationResolutionStatus.RECOVERABLE) == "waiting"
    assert format_integration_label(IntegrationResolutionStatus.EXHAUSTED) == "intervention"


def test_integration_action_reintegrate_distinct_from_conflict() -> None:
    """Reintegrating against target produces 'integration' label, not 'conflict resolution'."""
    label = format_integration_label(IntegrationAction.REINTEGRATE_AGAINST_TARGET)
    assert label == "integration"

    op_state = integration_action_to_operation_display_state(
        IntegrationAction.REINTEGRATE_AGAINST_TARGET
    )
    assert op_state == OperationDisplayState.INTEGRATING


def test_integration_display_label_enum_has_seven_states() -> None:
    """IntegrationDisplayLabel contains exactly seven distinct labels."""
    assert len(IntegrationDisplayLabel) == 7
    expected = {
        "conflict resolution",
        "evidence diagnosis",
        "integration",
        "landing",
        "publication",
        "waiting",
        "intervention",
    }
    assert set(IntegrationDisplayLabel) == expected
