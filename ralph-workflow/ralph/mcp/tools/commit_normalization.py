"""Fresh-evidence normalization helpers for commit-message artifact handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.commit_message_normalizer import normalize_commit_message_draft
from ralph.prompts.commit_evidence import CommitEvidenceBundle, build_commit_evidence_bundle

if TYPE_CHECKING:
    from pathlib import Path


def normalize_commit_submission(
    artifact_type: str, content: str, workspace_root: Path, *, draft_revision: int = 0
) -> tuple[str, dict[str, object] | None]:
    """Normalize once and return the exact receipt audit for that pass."""
    if artifact_type != "commit_message" or not (workspace_root / ".git").exists():
        return content, None
    evidence = _build_evidence(workspace_root)
    result = normalize_commit_message_draft(content, evidence, draft_revision=draft_revision)
    return result.content, {
        "content": result.content,
        "changed_files": list(evidence.changed_files),
        "change_areas": list(evidence.change_areas),
        "verification_facts": list(evidence.verification_facts),
        "behavior_facts": list(evidence.behavior_facts),
        "compatibility_hints": list(evidence.compatibility_hints),
        "risk_hints": list(evidence.risk_hints),
        "confidence": result.confidence,
        "transformations": [
            {"action": item.action, "source": item.source, "confidence": item.confidence}
            for item in result.transformations
        ],
        "provenance": result.provenance,
        "draft_revision": draft_revision,
    }


def normalize_commit_content(artifact_type: str, content: str, workspace_root: Path) -> str:
    """Compatibility wrapper for callers that need only normalized markdown."""
    normalized, _ = normalize_commit_submission(artifact_type, content, workspace_root)
    return normalized


def commit_normalization_audit(
    artifact_type: str, content: str, workspace_root: Path
) -> dict[str, object] | None:
    """Compatibility wrapper that returns one fresh normalization audit."""
    try:
        _, audit = normalize_commit_submission(artifact_type, content, workspace_root)
    except ValueError as exc:
        return {"status": "regeneration_required", "diagnostic": str(exc)}
    return audit


def _build_evidence(workspace_root: Path) -> CommitEvidenceBundle:
    return build_commit_evidence_bundle(workspace_root)


__all__ = ["commit_normalization_audit", "normalize_commit_content", "normalize_commit_submission"]
