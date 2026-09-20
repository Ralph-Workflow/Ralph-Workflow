"""Evidence-grounded intermediate representation for commit artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.prompts.commit_evidence import CommitEvidenceBundle, CommitMessageBudget


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
    fact_provenance: tuple[tuple[str, str, str, str], ...] = ()
    message_budget: CommitMessageBudget | None = None


def build_commit_message_ir(evidence: CommitEvidenceBundle, *, subject: str) -> CommitMessageIR:
    """Build a grounded IR; all non-subject claims come from live evidence."""
    rationale = tuple(f"Changed {area}." for area in evidence.change_areas)
    behavior_risk = (*evidence.behavior_facts, *evidence.compatibility_hints, *evidence.risk_hints)
    verification = evidence.verification_facts
    return CommitMessageIR(
        subject, evidence.change_areas, rationale, behavior_risk, verification,
        evidence.changed_files, fact_provenance=evidence.fact_provenance,
        message_budget=evidence.message_budget,
    )


def _body_items(ir: CommitMessageIR) -> tuple[str, ...]:
    """Cover grounded categories before applying the evidence-derived cap."""
    categories = tuple(parts for parts in (ir.rationale, ir.behavior_risk, ir.verification) if parts)
    if not categories:
        return ()
    # The artifact reader exposes only the primary Body item, so preserve all
    # grounded categories by consolidating rather than serializing siblings.
    return ("; ".join(part for category in categories for part in category),)


def render_commit_message_artifact(ir: CommitMessageIR) -> str:
    """Render the IR into the canonical commit_message Markdown grammar."""
    lines = ["---", "type: commit", f"subject: {ir.intent}", "---"]
    if body_items := _body_items(ir):
        lines.extend(("", "## Body", *(f"- [B-{index}] {item}" for index, item in enumerate(body_items, 1))))
    if ir.files:
        lines.extend(("", "## Files", *(f"- [F-{index}] {path}" for index, path in enumerate(ir.files, 1))))
    if ir.excluded_files:
        lines.extend(("", "## Excluded Files", *(f"- [X-{index}] {path} | {reason}" for index, (path, reason) in enumerate(ir.excluded_files, 1))))
    return "\n".join(lines) + "\n"


__all__ = ["CommitMessageIR", "build_commit_message_ir", "render_commit_message_artifact"]
