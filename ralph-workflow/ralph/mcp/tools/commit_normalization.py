from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from ralph.mcp.artifacts.commit_message_normalizer import normalize_commit_message_draft
from ralph.prompts.commit_evidence import (
    DEFAULT_COMMIT_EVIDENCE_SOURCE,
    CommitEvidenceBundle,
    CommitEvidenceSource,
    build_commit_evidence_bundle,
)

if TYPE_CHECKING:
    from pathlib import Path


class _EvidenceBuilder(Protocol):
    def __call__(self, root: Path, *args: object, **kwargs: object) -> CommitEvidenceBundle: ...


def _invoke_evidence_builder(
    builder: object,
    root: Path,
    source: CommitEvidenceSource,
) -> CommitEvidenceBundle:
    if callable(builder):
        fn = cast("_EvidenceBuilder", builder)
        try:
            return fn(root, source)
        except TypeError:
            return fn(root)
    raise TypeError(f"expected callable evidence builder, got {type(builder).__name__}")


def normalize_commit_submission(
    artifact_type: str,
    content: str,
    workspace_root: Path,
    *,
    draft_revision: int = 0,
    commit_evidence_source: CommitEvidenceSource = DEFAULT_COMMIT_EVIDENCE_SOURCE,
) -> tuple[str, dict[str, object] | None]:
    if artifact_type != "commit_message" or not (workspace_root / ".git").exists():
        return content, None
    evidence = _invoke_evidence_builder(_build_evidence, workspace_root, commit_evidence_source)
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


def normalize_commit_content(
    artifact_type: str,
    content: str,
    workspace_root: Path,
    *,
    commit_evidence_source: CommitEvidenceSource = DEFAULT_COMMIT_EVIDENCE_SOURCE,
) -> str:
    normalized, _ = normalize_commit_submission(
        artifact_type,
        content,
        workspace_root,
        commit_evidence_source=commit_evidence_source,
    )
    return normalized


def commit_normalization_audit(
    artifact_type: str,
    content: str,
    workspace_root: Path,
    *,
    commit_evidence_source: CommitEvidenceSource = DEFAULT_COMMIT_EVIDENCE_SOURCE,
) -> dict[str, object] | None:
    try:
        _, audit = normalize_commit_submission(
            artifact_type,
            content,
            workspace_root,
            commit_evidence_source=commit_evidence_source,
        )
    except ValueError as exc:
        return {"status": "regeneration_required", "diagnostic": str(exc)}
    return audit


def _build_evidence(
    workspace_root: Path,
    source: CommitEvidenceSource = DEFAULT_COMMIT_EVIDENCE_SOURCE,
) -> CommitEvidenceBundle:
    return build_commit_evidence_bundle(workspace_root, source=source)


__all__ = ["commit_normalization_audit", "normalize_commit_content", "normalize_commit_submission"]
