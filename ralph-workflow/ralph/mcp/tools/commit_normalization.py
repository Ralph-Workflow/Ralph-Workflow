"""Fresh-evidence normalization helpers for commit-message artifact handlers."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from ralph.mcp.artifacts.commit_message_normalizer import normalize_commit_message_draft

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.prompts.commit_evidence import CommitEvidenceBundle


class _CommitEvidenceModule(Protocol):
    def build_commit_evidence_bundle(self, workspace_root: Path) -> CommitEvidenceBundle: ...


def normalize_commit_content(artifact_type: str, content: str, workspace_root: Path) -> str:
    """Normalize repairable Git-backed drafts and preserve precise ambiguity errors."""
    if artifact_type != "commit_message" or not (workspace_root / ".git").exists():
        return content
    return normalize_commit_message_draft(content, _build_evidence(workspace_root)).content


def _build_evidence(workspace_root: Path) -> CommitEvidenceBundle:
    """Load prompt evidence after the MCP import graph has initialized."""
    module = cast(
        "_CommitEvidenceModule", import_module("ralph.prompts.commit_evidence")
    )
    return module.build_commit_evidence_bundle(workspace_root)


def commit_normalization_audit(
    artifact_type: str, content: str, workspace_root: Path
) -> dict[str, object] | None:
    """Return receipt audit data or a precise regeneration diagnostic."""
    if artifact_type != "commit_message" or not (workspace_root / ".git").exists():
        return None
    evidence = _build_evidence(workspace_root)
    try:
        result = normalize_commit_message_draft(content, evidence)
    except ValueError as exc:
        return {"status": "regeneration_required", "diagnostic": str(exc)}
    return {
        "content": result.content,
        "changed_files": list(evidence.changed_files),
        "change_areas": list(evidence.change_areas),
        "verification_facts": list(evidence.verification_facts),
        "behavior_facts": list(evidence.behavior_facts),
        "compatibility_hints": list(evidence.compatibility_hints),
        "risk_hints": list(evidence.risk_hints),
        "confidence": result.confidence,
        "transformations": list(result.transformations),
    }
