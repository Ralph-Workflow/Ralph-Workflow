from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.auto_integrate_record import (
    AUTO_INTEGRATE_RECORD_FILENAME,
    RAISE_DIAGNOSTIC_INTERVENTION,
    IntegrationRecord,
    parse_record_payload,
    read_record,
)


def _write_legacy_record(
    tmp_path: Path,
    payload: dict[str, object],
) -> Path:
    record_file = tmp_path / ".agent" / AUTO_INTEGRATE_RECORD_FILENAME
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text(json.dumps(payload), encoding="utf-8")
    return record_file


def _global_evidence_payload() -> dict[str, object]:
    return {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "owning_worktree": "/tmp/scope",
        "diagnostic_evidence": [["for-each-ref", "deadbeef"]],
    }


def test_legacy_global_evidence_loader_raises_through_transition_table(
    tmp_path: Path,
) -> None:
    _write_legacy_record(tmp_path, _global_evidence_payload())
    loaded = read_record(tmp_path)
    assert loaded is not None
    assert loaded.diagnostic_intervention_required is False
    assert dict(loaded.diagnostic_evidence) == {}


def test_legacy_failure_cause_coercer_preserves_typed_default() -> None:
    payload1: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
    }
    rec1 = parse_record_payload(payload1)
    assert rec1 is not None
    assert rec1.last_failure_cause == ResolutionFailureCause.NONE

    payload2: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "last_reason": "resolution_agent_failure",
    }
    rec2 = parse_record_payload(payload2)
    assert rec2 is not None
    assert rec2.last_failure_cause == ResolutionFailureCause.AGENT_FAILURE

    payload4: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "last_failure_cause": "UNRESOLVED_CONFLICT",
    }
    rec4 = parse_record_payload(payload4)
    assert rec4 is not None
    assert rec4.last_failure_cause == ResolutionFailureCause.UNRESOLVED_CONFLICT


def test_audit_visible_transition_observable_through_with_transition() -> None:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
    )
    assert record.diagnostic_intervention_required is False
    assert record.repair_cap_reason is None

    new_record = record.with_transition(
        RAISE_DIAGNOSTIC_INTERVENTION,
        diagnostic_intervention_required=True,
        repair_cap_reason="closed-transition table applied",
    )
    assert new_record is not record
    assert new_record.diagnostic_intervention_required is True
    assert new_record.repair_cap_reason == "closed-transition table applied"
    assert record.diagnostic_intervention_required is False
    assert record.repair_cap_reason is None

    with pytest.raises(ValueError, match="does not accept field"):
        record.with_transition(
            RAISE_DIAGNOSTIC_INTERVENTION,
            phase="integrated",
        )
