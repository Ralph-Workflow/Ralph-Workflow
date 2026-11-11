
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyFinding:

    requirement_id: str
    path: str
    missing_evidence: str
    required_outcome: str
    blocking: bool = True

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "requirement_id": self.requirement_id,
            "path": self.path,
            "missing_evidence": self.missing_evidence,
            "required_outcome": self.required_outcome,
            "blocking": self.blocking,
        }


__all__ = ["PolicyFinding"]
