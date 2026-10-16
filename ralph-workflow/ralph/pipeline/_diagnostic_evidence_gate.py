"""Single source for diagnostic evidence gate retention reasons."""

from ralph.pipeline.operation_evidence import EvidenceVerdict

EVIDENCE_CHANGED_REASON = (
    "diagnostic evidence changed; original evidence must be restored before recovery"
)
EVIDENCE_UNREADABLE_REASON = "diagnostic evidence unreadable; recovery retained"


def diagnostic_evidence_gate_reason(verdict: EvidenceVerdict) -> str:
    """Map a non-UNCHANGED evidence verdict to its retention reason."""
    if verdict == EvidenceVerdict.CHANGED:
        return EVIDENCE_CHANGED_REASON
    return EVIDENCE_UNREADABLE_REASON
