from __future__ import annotations

import pytest

from ralph.config.models import UnifiedConfig
from ralph.display.auto_integrate_message import format_integration_label
from ralph.display.integration_display_label import IntegrationDisplayLabel
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.pipeline import run_loop
from ralph.pipeline._integration_action import (
    RebaseOutcomeKind,
    next_integration_action,
)
from ralph.pipeline.auto_integrate_record import (
    AUTO_INTEGRATE_RECORD_FILENAME,
    IntegrationRecord,
    RecordReadOutcomeDisposition,
    read_record_outcome,
)
from ralph.pipeline.integration_resolution_status import IntegrationResolutionStatus
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

pytestmark = pytest.mark.subprocess_e2e

UNREADABLE_CAUSES: tuple[str, ...] = ("corrupt_json", "unsupported_schema")


def _conflict_resolution_state() -> RebaseState:
    state = RebaseState(last_outcome=RebaseOutcomeKind.CONFLICT, resolving_rebase=True)
    assert state.operation_display_state == "conflict_resolution"
    return state


def _evidence_diagnosis_state() -> RebaseState:
    state = RebaseState(last_outcome=RebaseOutcomeKind.DIAGNOSING)
    assert state.operation_display_state == "evidence_diagnosis"
    return state


def _integration_state() -> RebaseState:
    state = RebaseState(last_outcome=RebaseOutcomeKind.INTEGRATING)
    assert state.operation_display_state == "integrating"
    return state


def _landing_state() -> RebaseState:
    state = RebaseState(last_outcome=RebaseOutcomeKind.REBASED, fast_forwarded=False)
    assert state.operation_display_state == "landing"
    return state


def _publication_state() -> RebaseState:
    state = RebaseState(last_publication_decision="publish_now")
    assert state.operation_display_state == "publishing"
    return state


def _waiting_state() -> RebaseState:
    state = RebaseState(last_outcome=RebaseOutcomeKind.WAITING)
    assert state.operation_display_state == "waiting"
    return state


def _intervention_state() -> RebaseState:
    state = RebaseState(
        last_outcome=RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
        operation_display_state="intervention_required",
    )
    assert state.operation_display_state == "intervention_required"
    return state


ALL_SEVEN_STATES: dict[str, RebaseState] = {
    "conflict_resolution": _conflict_resolution_state(),
    "evidence_diagnosis": _evidence_diagnosis_state(),
    "integration": _integration_state(),
    "landing": _landing_state(),
    "publication": _publication_state(),
    "waiting": _waiting_state(),
    "intervention": _intervention_state(),
}


def test_seven_real_lifecycle_projections_have_pairwise_distinct_labels() -> None:
    labels = {key: format_integration_label(state) for key, state in ALL_SEVEN_STATES.items()}
    expected = {label.value for label in IntegrationDisplayLabel}
    assert set(labels.values()) == expected
    assert len(set(labels.values())) == 7


@pytest.mark.parametrize("key", sorted(ALL_SEVEN_STATES))
def test_label_is_the_canonical_enum_value(key: str) -> None:
    state = ALL_SEVEN_STATES[key]
    label = format_integration_label(state)
    assert label == IntegrationDisplayLabel(label)


def test_record_status_integrating_maps_to_integration_label() -> None:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
    )
    assert record.is_integrating is True
    label = format_integration_label(next_integration_action(record))
    assert label == IntegrationDisplayLabel.INTEGRATION.value
    assert label != IntegrationDisplayLabel.LANDING.value


@pytest.mark.parametrize("cause", UNREADABLE_CAUSES)
def test_unreadable_evidence_projects_to_ladder_escalation_label(
    tmp_path: object, cause: str
) -> None:
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "t"],
    ):
        assert (
            run_git(
                args,
                cwd=tmp_path,
                label="display-lifecycle-test",
                options=GitRunOptions(timeout=5),
            ).returncode
            == 0
        )
    record_file = tmp_path / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME
    record_file.parent.mkdir(parents=True)
    if cause == "corrupt_json":
        record_file.write_bytes(b"{not json")
    else:
        record_file.write_bytes(b'{"totally_unknown_shape": [1, 2, 3]}')

    outcome = read_record_outcome(tmp_path)
    assert outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE
    assert outcome.unreadable_reason == cause

    recovered = run_loop._run_auto_integrate_recovery_preamble(
        WorkspaceScope(tmp_path), UnifiedConfig()
    )
    assert recovered is not None
    assert recovered.last_outcome.value == "ladder_escalation"
    assert format_integration_label(recovered) == IntegrationDisplayLabel.EVIDENCE_DIAGNOSIS.value
    assert format_integration_label(recovered) not in (
        IntegrationDisplayLabel.INTERVENTION.value,
        IntegrationDisplayLabel.CONFLICT_RESOLUTION.value,
    )
    assert record_file.read_bytes() in (b"{not json", b'{"totally_unknown_shape": [1, 2, 3]}')


def test_resolution_status_projects_to_distinct_labels() -> None:
    assert (
        format_integration_label(IntegrationResolutionStatus.RESOLVED)
        == IntegrationDisplayLabel.LANDING.value
    )
    assert (
        format_integration_label(IntegrationResolutionStatus.RECOVERABLE)
        == IntegrationDisplayLabel.WAITING.value
    )
    assert (
        format_integration_label(IntegrationResolutionStatus.EXHAUSTED)
        == IntegrationDisplayLabel.INTERVENTION.value
    )


def test_integration_action_display_state_uses_closed_vocabulary() -> None:
    from ralph.display.auto_integrate_message import (
        OperationDisplayState,
        integration_action_to_operation_display_state,
    )

    assert (
        integration_action_to_operation_display_state("diagnosing")
        is OperationDisplayState.EVIDENCE_DIAGNOSIS
    )
    assert (
        integration_action_to_operation_display_state("evidence_diagnosis")
        is OperationDisplayState.EVIDENCE_DIAGNOSIS
    )

    assert (
        integration_action_to_operation_display_state("anything_arbitrary", is_diagnosis=True)
        is OperationDisplayState.EVIDENCE_DIAGNOSIS
    )

    assert integration_action_to_operation_display_state("diagnostic evidence pending") is None
    assert integration_action_to_operation_display_state("evidence collection in progress") is None
    assert integration_action_to_operation_display_state("diagnos") is None

    assert (
        integration_action_to_operation_display_state("conflict_resolution")
        is OperationDisplayState.CONFLICT_RESOLUTION
    )
    assert (
        integration_action_to_operation_display_state("land_target")
        is OperationDisplayState.LANDING
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
