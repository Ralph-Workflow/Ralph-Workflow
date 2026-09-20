"""Evidence-grounded intermediate representation for commit artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.prompts.commit_evidence import CommitEvidenceBundle


@dataclass(frozen=True)
class CommitMessageIR:
    """Canonical factual fields used to render a commit-message artifact."""

    intent: str
    change_areas: tuple[str, ...]
    rationale: tuple[str, ...]
    behavior_risk: tuple[str, ...]
    verification: tuple[str, ...]
    files: tuple[str, ...]
    excluded_files: tuple[tuple[str, str], ...] = ()


def build_commit_message_ir(evidence: CommitEvidenceBundle, *, subject: str) -> CommitMessageIR:
    """Build the smallest factual IR from authoritative commit evidence."""
    return CommitMessageIR(
        intent=subject,
        change_areas=evidence.change_areas,
        rationale=(),
        behavior_risk=(*evidence.compatibility_hints, *evidence.risk_hints),
        verification=evidence.verification_hints,
        files=evidence.changed_files,
    )


def render_commit_message_artifact(ir: CommitMessageIR) -> str:
    """Render an IR into the canonical commit_message Markdown grammar."""
    lines = ["---", "type: commit", f"subject: {ir.intent}", "---"]
    body_parts = (*ir.rationale, *ir.behavior_risk, *ir.verification)
    if body_parts:
        lines.extend(("", "## Body", f"- [B-1] {'; '.join(body_parts)}"))
    if ir.files:
        lines.extend(("", "## Files", *(f"- [F-{index}] {path}" for index, path in enumerate(ir.files, 1))))
    if ir.excluded_files:
        lines.extend(
            ("", "## Excluded Files", *(f"- [X-{index}] {path} | {reason}" for index, (path, reason) in enumerate(ir.excluded_files, 1)))
        )
    return "\n".join(lines) + "\n"


__all__ = ["CommitMessageIR", "build_commit_message_ir", "render_commit_message_artifact"]
