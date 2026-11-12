from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReadinessAssessment:
    structure_complete: bool = False
    implementation_verified: bool = False
    outcome_evidence_reviewed: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "structure_complete": self.structure_complete,
            "implementation_verified": self.implementation_verified,
            "outcome_evidence_reviewed": self.outcome_evidence_reviewed,
        }


__all__ = ["ReadinessAssessment"]
