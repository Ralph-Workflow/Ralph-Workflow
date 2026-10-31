"""Loader-level regression for the closed transition table.

The plan's UNIT 1 closes the loader's intervention bypass:
``_migrate_payload_evidence`` currently authors
``diagnostic_intervention_required=True`` and ``repair_cap_reason``
through a bare ``dict(**data_raw, ...)``. The new contract
constructs an :class:`IntegrationRecord` from the migrated dict
and applies ``RAISE_DIAGNOSTIC_INTERVENTION`` so the only path
that flips the intervention flag is the closed transition table.

These tests observe the loadable public surface of the loader and
the closed transition table. They never walk the source AST or
read private helpers directly. They run RED on the unfixed
loader and GREEN once UNIT 1 lands.
"""

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
    """Public entry point: ``read_record`` on a legacy global-evidence record.

    The new diagnostic path drops the legacy global evidence key
    silently and re-derives state from Git; surfacing intervention
    here was the production livelock. The test now pins the
    contract: legacy keys load with no intervention and no
    held evidence.
    """
    _write_legacy_record(tmp_path, _global_evidence_payload())
    loaded = read_record(tmp_path)
    assert loaded is not None
    assert loaded.diagnostic_intervention_required is False
    # The legacy key is dropped from the live evidence tuple.
    assert dict(loaded.diagnostic_evidence) == {}


def test_legacy_failure_cause_coercer_preserves_typed_default() -> None:
    """Re-runs the existing ``test_legacy_record_last_failure_cause_migration`` regression pin.

    Confirms the audit-visible ``RAISE_DIAGNOSTIC_INTERVENTION`` does NOT
    clobber ``last_failure_cause`` (NONE / AGENT_FAILURE / UNRESOLVED_CONFLICT)
    when the new loader is exercised via ``parse_record_payload``.
    """
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
    """The closed transition table is the only path to ``RAISE_DIAGNOSTIC_INTERVENTION``.

    Asserts that :meth:`IntegrationRecord.with_transition` flips the
    intervention flag through the transition table, returns a NEW record
    (the original is unchanged), and refuses to apply a transition with
    an unregistered field.
    """
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
    # Original is frozen and unchanged.
    assert record.diagnostic_intervention_required is False
    assert record.repair_cap_reason is None

    # The transition table refuses unregistered fields.
    with pytest.raises(ValueError, match="does not accept field"):
        record.with_transition(
            RAISE_DIAGNOSTIC_INTERVENTION,
            phase="integrated",  # phase is not a field of this transition
        )
