"""Fresh-evidence normalization helpers for commit-message artifact handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.commit_message_normalizer import normalize_commit_message_draft
from ralph.prompts.commit_evidence import build_commit_evidence_bundle

if TYPE_CHECKING:
    from pathlib import Path


def normalize_commit_content(artifact_type: str, content: str, workspace_root: Path) -> str:
    """Normalize a Git-backed commit draft while preserving ambiguous content."""
    if artifact_type != "commit_message" or not (workspace_root / ".git").exists():
        return content
    try:
        return normalize_commit_message_draft(
            content, build_commit_evidence_bundle(workspace_root)
        ).content
    except ValueError:
        return content


def commit_normalization_audit(
    artifact_type: str, content: str, workspace_root: Path
) -> dict[str, object] | None:
    """Return receipt audit data for normalized Git-backed commit content."""
    if artifact_type != "commit_message" or not (workspace_root / ".git").exists():
        return None
    evidence = build_commit_evidence_bundle(workspace_root)
    result = normalize_commit_message_draft(content, evidence)
    return {
        "content": result.content,
        "changed_files": list(evidence.changed_files),
        "change_areas": list(evidence.change_areas),
        "verification_hints": list(evidence.verification_hints),
        "confidence": result.confidence,
        "transformations": list(result.transformations),
    }
