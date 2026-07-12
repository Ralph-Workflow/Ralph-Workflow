"""PolicyFinding dataclass.

A single deterministic validation finding. Each finding carries a stable
``requirement_id`` (one of the ``ID_*`` prefixes defined in
:mod:`ralph.project_policy.markers`), a path, the missing evidence, and the
required remediation outcome. The validator emits exactly one finding per
``(requirement_id, path)`` pair so the remediation agent can address
findings deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyFinding:
    """A single deterministic validation finding.

    Attributes:
        requirement_id: Stable ``RWP-*`` identifier. The validator emits
            exactly one finding per ``(requirement_id, path)`` pair so the
            remediation agent can address findings deterministically.
        path: Workspace-relative path of the affected file or concern (e.g.
            ``docs/ralph-workflow-policy/testing-policy.md``).
        missing_evidence: Short, human-readable description of what is
            missing or invalid. Surfaced directly in the BLOCKED report and
            the remediation prompt.
        required_outcome: What an agent must do to clear the finding.
    """

    requirement_id: str
    path: str
    missing_evidence: str
    required_outcome: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable dict representation."""
        return {
            "requirement_id": self.requirement_id,
            "path": self.path,
            "missing_evidence": self.missing_evidence,
            "required_outcome": self.required_outcome,
        }


__all__ = ["PolicyFinding"]
