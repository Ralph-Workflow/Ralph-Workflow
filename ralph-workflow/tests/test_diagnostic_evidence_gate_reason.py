"""The diagnostic evidence gate retention reason has a single definition site."""

from ralph.pipeline._diagnostic_evidence_gate import (
    EVIDENCE_CHANGED_REASON,
    EVIDENCE_UNREADABLE_REASON,
    diagnostic_evidence_gate_reason,
)
from ralph.pipeline.operation_evidence import EvidenceVerdict


def test_changed_verdict_maps_to_changed_reason():
    assert diagnostic_evidence_gate_reason(EvidenceVerdict.CHANGED) == EVIDENCE_CHANGED_REASON
    assert EVIDENCE_CHANGED_REASON == (
        "diagnostic evidence lease unparseable; manual recovery required"
    )


def test_other_verdicts_map_to_unreadable_reason():
    assert diagnostic_evidence_gate_reason(EvidenceVerdict.UNKNOWN) == EVIDENCE_UNREADABLE_REASON
    assert EVIDENCE_UNREADABLE_REASON == "diagnostic evidence lease expired; re-deriving from Git"
