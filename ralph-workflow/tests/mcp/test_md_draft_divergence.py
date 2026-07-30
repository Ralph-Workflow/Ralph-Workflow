"""Unit coverage for the unsubmitted markdown draft comparison."""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.artifacts.md_draft_io import (
    UnsubmittedDraftDivergence,
    save_md_draft,
    unsubmitted_draft_divergence,
)
from tests.test_tool_artifact_2_helper_memorybackend import MemoryBackend


def test_unsubmitted_draft_divergence_handles_every_persistence_branch() -> None:
    """S-2: only a retained draft differing from canonical is divergence."""
    backend = MemoryBackend()
    artifact_dir = Path("/virtual/.agent/artifacts")
    canonical = artifact_dir / "plan.md"

    assert unsubmitted_draft_divergence(artifact_dir, "plan", canonical, backend=backend) is None

    save_md_draft(artifact_dir, "plan", "draft", backend=backend)
    assert unsubmitted_draft_divergence(artifact_dir, "plan", canonical, backend=backend) is None

    backend.write_text(canonical, "draft")
    assert unsubmitted_draft_divergence(artifact_dir, "plan", canonical, backend=backend) is None

    backend.write_text(canonical, "submitted")
    assert unsubmitted_draft_divergence(artifact_dir, "plan", canonical, backend=backend) == (
        UnsubmittedDraftDivergence(draft_chars=5, canonical_chars=9)
    )
