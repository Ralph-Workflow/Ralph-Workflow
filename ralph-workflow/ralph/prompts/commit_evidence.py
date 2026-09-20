"""Compatibility import surface for commit evidence."""

from pathlib import Path

from ralph.prompts import _commit_evidence as _impl

CommitEvidenceBundle = _impl.CommitEvidenceBundle
CommitMessageBudget = _impl.CommitMessageBudget


def build_commit_evidence_bundle(repo_root: Path) -> CommitEvidenceBundle:
    """Build evidence while retaining this legacy monkeypatch surface."""
    original_paths = _impl.list_changed_paths
    original_diff = _impl.commit_generation_diff
    _impl.list_changed_paths = list_changed_paths
    _impl.commit_generation_diff = commit_generation_diff
    try:
        return _impl.build_commit_evidence_bundle(repo_root)
    finally:
        _impl.list_changed_paths = original_paths
        _impl.commit_generation_diff = original_diff


list_changed_paths = _impl.list_changed_paths
commit_generation_diff = _impl.commit_generation_diff

__all__ = ["CommitEvidenceBundle", "CommitMessageBudget", "build_commit_evidence_bundle"]
