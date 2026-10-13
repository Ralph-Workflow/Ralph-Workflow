"""Three default-false dimensions of post-validation readiness.

Single-class module per the repository's class-per-module policy. The
only path to a ``True`` dimension runs through the assembly logic in
:mod:`ralph.project_policy._assessment`, which itself fails closed on
any mismatch.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReadinessAssessment:
    """Three default-false dimensions of post-validation readiness.

    All three default to ``False`` so an uninitialised assessment cannot
    accidentally report verified work. The only path to ``True`` runs
    through the assembly logic in :func:`ralph.project_policy.pipeline_driver._assess`,
    which itself fails closed on any mismatch.
    """

    structure_complete: bool = False
    implementation_verified: bool = False
    outcome_evidence_reviewed: bool = False

    def as_dict(self) -> dict[str, bool]:
        """Return the dimensions in a stable, JSON-friendly shape."""
        return {
            "structure_complete": self.structure_complete,
            "implementation_verified": self.implementation_verified,
            "outcome_evidence_reviewed": self.outcome_evidence_reviewed,
        }


__all__ = ["ReadinessAssessment"]
