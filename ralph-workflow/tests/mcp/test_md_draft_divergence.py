"""Unit coverage for the unsubmitted markdown draft comparison."""

from __future__ import annotations

from pathlib import Path

from ralph.agents.completion_signals import completion_signals_terminal, evaluate_completion
from ralph.mcp.artifacts.completion_receipts import write_artifact_receipt
from ralph.mcp.artifacts.md_draft_io import (
    UnsubmittedDraftDivergence,
    is_md_draft_seeded,
    mark_md_draft_seeded,
    save_md_draft,
    unsubmitted_draft_divergence,
)
from ralph.mcp.artifacts.state_db import RunStateDB
from ralph.phases.required_artifacts import RequiredArtifact
from tests._tool_artifact_2_helper_memorybackend import MemoryBackend


def _worker_plan() -> RequiredArtifact:
    return RequiredArtifact(
        phase="planning",
        artifact_type="plan",
        artifact_path=".agent/workers/unit-api/artifacts/plan.md",
        markdown_path=None,
        normalizer=None,
        artifact_required=True,
    )


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


def test_md_draft_save_regression_identical_seeded_replay_preserves_provenance() -> None:
    """S-3: replaying an unchanged seeded draft does not remove its provenance marker."""
    backend = MemoryBackend()
    artifact_dir = Path("/virtual/.agent/artifacts")

    save_md_draft(artifact_dir, "plan", "canonical content", backend=backend)
    mark_md_draft_seeded(artifact_dir, "plan", backend=backend)

    save_md_draft(artifact_dir, "plan", "canonical content", backend=backend)

    assert is_md_draft_seeded(artifact_dir, "plan", backend=backend)
    assert unsubmitted_draft_divergence(
        artifact_dir, "plan", artifact_dir / "plan.md", backend=backend
    ) is None


def test_md_draft_divergence_regression_worker_artifact_uses_its_own_directory(
    tmp_path: Path,
) -> None:
    """S-4: coordinator drafts must not mask or block worker submissions."""
    required = _worker_plan()
    worker_artifact_dir = tmp_path / ".agent" / "workers" / "unit-api" / "artifacts"
    worker_artifact_dir.mkdir(parents=True)
    worker_canonical = worker_artifact_dir / "plan.md"
    worker_canonical.write_text("submitted")
    save_md_draft(tmp_path / ".agent" / "artifacts", "plan", "stale coordinator draft")
    write_artifact_receipt(tmp_path, "run-1", "plan")
    state = RunStateDB(tmp_path)
    state.upsert_completion_sentinel("run-1", "sentinel")
    state.close()

    assert completion_signals_terminal(
        evaluate_completion(tmp_path, required_artifact=required, run_id="run-1")
    )

    save_md_draft(worker_artifact_dir, "plan", "unsubmitted worker draft")

    assert not completion_signals_terminal(
        evaluate_completion(tmp_path, required_artifact=required, run_id="run-1")
    )
