"""Fresh-evidence normalization helpers for commit-message artifact handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.commit_message_normalizer import normalize_commit_message_draft
from ralph.prompts.commit_evidence import build_commit_evidence_bundle

if TYPE_CHECKING:
    from pathlib import Path


def normalize_commit_content(artifact_type: str, content: str, workspace_root: Path) -> str:
    """Normalize repairable Git-backed drafts; retain ambiguous drafts for diagnostics."""
    if artifact_type != "commit_message" or not (workspace_root / ".git").exists():
        return content
    try:
        return normalize_commit_message_draft(content, build_commit_evidence_bundle(workspace_root)).content
    except ValueError:
        return content


def commit_normalization_audit(
    artifact_type: str, content: str, workspace_root: Path
) -> dict[str, object] | None:
    """Return receipt audit data or a precise regeneration diagnostic."""
    if artifact_type != "commit_message" or not (workspace_root / ".git").exists():
        return None
    evidence = build_commit_evidence_bundle(workspace_root)
    try:
        result = normalize_commit_message_draft(content, evidence)
    except ValueError as exc:
        return {"status": "regeneration_required", "diagnostic": str(exc)}
    return {
        "content": result.content,
        "changed_files": list(evidence.changed_files),
        "change_areas": list(evidence.change_areas),
        "verification_hints": list(evidence.verification_hints),
        "compatibility_hints": list(evidence.compatibility_hints),
        "risk_hints": list(evidence.risk_hints),
        "confidence": result.confidence,
        "transformations": list(result.transformations),
    }
