"""Public-boundary test for the seven-label operator display taxonomy.

Drives one representative lifecycle decision per category through
:func:`ralph.display.auto_integrate_message.format_integration_label`
(the single public display-label projection) and asserts the seven
labels are pairwise distinct.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from ralph.display.auto_integrate_message import format_integration_label
from ralph.display.integration_display_label import IntegrationDisplayLabel
from ralph.pipeline.auto_integrate_record import (
    AUTO_INTEGRATE_RECORD_FILENAME,
    RecordReadOutcomeDisposition,
    read_record_outcome,
)


@dataclass(frozen=True)
class _FakeLifecycleState:
    """DI-backed fake standing in for the producer's display state."""

    last_action: str


def _drive(action: str) -> str:
    state: Any = _FakeLifecycleState(last_action=action)
    return format_integration_label(state)


def test_seven_lifecycle_labels_are_pairwise_distinct() -> None:
    """One representative per category, all seven labels distinct."""
    labels = {
        "conflict_resolution": _drive("conflict_resolution"),
        "evidence_diagnosis": _drive("evidence_diagnosis"),
        "integration": _drive("integrating"),
        "landing": _drive("landing"),
        "publication": _drive("publishing"),
        "waiting": _drive("wait_for_bounded_retry"),
        "intervention": _drive("report_intervention_required"),
    }
    expected = {label.value for label in IntegrationDisplayLabel}
    assert set(labels.values()) == expected
    assert len(set(labels.values())) == len(labels) == 7


def test_record_status_integrating_maps_to_integration_label() -> None:
    """A record with status=integrating projects onto the integration label."""
    @dataclass(frozen=True)
    class _RecordState:
        operation_display_state: str

    state: Any = _RecordState(operation_display_state="integrating")
    assert format_integration_label(state) == IntegrationDisplayLabel.INTEGRATION.value


def test_unreadable_evidence_maps_to_intervention_label(
    tmp_path: Any,
) -> None:
    """UNREADABLE record evidence must project to the intervention label.

    The producer-side projection (U4b) routes the unreadable-evidence
    outcome onto ``report_intervention_required``; the display boundary
    then maps it to the intervention label. Until U4b lands, this
    asserts both halves of the contract independently: the record read
    yields UNREADABLE, and the intervention projection renders as
    ``intervention``.
    """
    record_dir = tmp_path / ".agent"
    record_dir.mkdir()
    corrupt = record_dir / AUTO_INTEGRATE_RECORD_FILENAME
    corrupt.write_text("{not json", encoding="utf-8")
    outcome = read_record_outcome(tmp_path)
    assert outcome.disposition == RecordReadOutcomeDisposition.UNREADABLE

    projection: Any = _FakeLifecycleState(last_action="report_intervention_required")
    assert format_integration_label(projection) == IntegrationDisplayLabel.INTERVENTION.value


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
