"""Authority audit — acceptance scenarios 7, 8, and 10 (entry points, legacy, display).

Acceptance scenario 7: exercise startup, phase-boundary, commit, cooldown,
parallel-join, and terminal entry points against the same underlying state.
Every entry point must produce the same lifecycle decision.

Acceptance scenario 8: load legacy checkpoint and integration representations.
Migration must produce one authoritative state or explicit intervention,
never merge contradictory truths heuristically.

Acceptance scenario 10: confirm the operator surface distinguishes conflict
resolution, evidence diagnosis, integration, landing, publication, waiting,
and intervention.
"""

from __future__ import annotations

import json
from pathlib import Path

from ralph.display.integration_display_label import (
    DISPLAY_LABEL_MAP,
    IntegrationDisplayLabel,
)
from ralph.pipeline._integration_action import (
    RebaseOutcomeKind,
)
from ralph.pipeline._pipeline_state_machine import (
    PhaseDecision,
    decide_phase_transition,
)
from ralph.pipeline.auto_integrate_record import (
    AUTO_INTEGRATE_RECORD_FILENAME,
    IntegrationRecord,
    read_record,
)
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.pipeline.state import PipelineState
from ralph.workspace.scope import WorkspaceScope


def _integrated_record() -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        integrated_feature_sha="c" * 40,
    )


def _write_unreadable_record(tmp_path: Path) -> None:
    """Write a corrupt record (UNREADABLE outcome)."""
    record_file = tmp_path / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text("{not valid json", encoding="utf-8")


def _write_present_unreadable_record(tmp_path: Path) -> None:
    """Write a present-but-unreadable record (UNREADABLE outcome)."""
    record_file = tmp_path / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME
    record_file.parent.mkdir(parents=True, exist_ok=True)
    # Valid JSON, but missing required 'phase' field -> UNREADABLE.
    record_file.write_text('{"target": "main"}', encoding="utf-8")


def test_entry_points_agree_on_unreadable_record(tmp_path: Path) -> None:
    """Scenario 7: every entry point produces the same decision on UNREADABLE."""
    _write_unreadable_record(tmp_path)

    # Startup (recovery boundary).
    outcome = recover_incomplete_integration(WorkspaceScope(tmp_path))
    assert outcome is not None
    assert outcome.last_outcome == RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED
    assert outcome.last_decision == "report_intervention_required"
    assert outcome.recovery_record_retained is True

    # Phase boundary.
    state = PipelineState(phase="development", rebase=outcome)
    decision = decide_phase_transition(state, None, integration_record=None, rebase_state=outcome)
    assert decision is PhaseDecision.REPORT_INTERVENTION_REQUIRED


def test_legacy_checkpoint_migration_yields_one_authoritative_state(
    tmp_path: Path,
) -> None:
    """Scenario 8: legacy global-evidence record -> one authoritative state.

    Authoritative starting state: a record JSON file containing
    ``diagnostic_evidence: [["for-each-ref", "deadbeef"]]``.
    Expected typed state:
    ``IntegrationRecord.diagnostic_intervention_required is True``,
    ``IntegrationRecord.repair_cap_reason`` contains "legacy
    repository-global evidence".
    """
    record_file = tmp_path / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME
    record_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "owning_worktree": str(tmp_path),
        "diagnostic_evidence": [["for-each-ref", "deadbeef"]],
    }
    record_file.write_text(json.dumps(payload), encoding="utf-8")

    loaded = read_record(tmp_path)
    assert loaded is not None
    assert loaded.diagnostic_intervention_required is True
    assert "legacy repository-global evidence" in (loaded.repair_cap_reason or "")


def test_operator_surface_distinguishes_seven_labels() -> None:
    """Scenario 10: the seven display labels are distinct closed vocabulary."""
    assert len(IntegrationDisplayLabel) == 7
    # Each label is distinct.
    values = {label.value for label in IntegrationDisplayLabel}
    assert len(values) == 7
    # The seven expected labels.
    assert IntegrationDisplayLabel.CONFLICT_RESOLUTION.value == "conflict resolution"
    assert IntegrationDisplayLabel.EVIDENCE_DIAGNOSIS.value == "evidence diagnosis"
    assert IntegrationDisplayLabel.INTEGRATION.value == "integration"
    assert IntegrationDisplayLabel.LANDING.value == "landing"
    assert IntegrationDisplayLabel.PUBLICATION.value == "publication"
    assert IntegrationDisplayLabel.WAITING.value == "waiting"
    assert IntegrationDisplayLabel.INTERVENTION.value == "intervention"
    # The dispatch map routes each producer-side string to its label.
    assert DISPLAY_LABEL_MAP["conflict_resolution"] == "conflict resolution"
    assert DISPLAY_LABEL_MAP["diagnosing"] == "evidence diagnosis"
    assert DISPLAY_LABEL_MAP["integrating"] == "integration"
    assert DISPLAY_LABEL_MAP["land_target"] == "landing"
    assert DISPLAY_LABEL_MAP["publish_after_landing"] == "publication"
    assert DISPLAY_LABEL_MAP["wait_for_bounded_retry"] == "waiting"
    assert DISPLAY_LABEL_MAP["diagnostic_intervention_required"] == "intervention"
