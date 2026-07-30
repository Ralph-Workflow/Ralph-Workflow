"""Worker completion must resolve fallback evidence in the worker namespace."""

from pathlib import Path

from ralph.phases.required_artifacts import RequiredArtifact
from ralph.pipeline.effect_executor import required_artifact_for_invocation


def test_required_artifact_is_scoped_to_worker_artifact_directory() -> None:
    worker_artifact_dir = Path(".agent/workers/unit-api/artifacts")
    required = RequiredArtifact(
        phase="development",
        artifact_type="development_result",
        artifact_path=".agent/artifacts/development_result.md",
        markdown_path=".agent/DEVELOPMENT_RESULT.md",
        normalizer=None,
    )

    scoped = required_artifact_for_invocation(required, worker_artifact_dir)

    assert scoped is not None
    assert scoped.artifact_path == str(worker_artifact_dir / "development_result.md")
    assert scoped.markdown_path == str(
        worker_artifact_dir.parent / "handoffs" / "DEVELOPMENT_RESULT.md"
    )
    assert required.artifact_path == ".agent/artifacts/development_result.md"
    assert required.markdown_path == ".agent/DEVELOPMENT_RESULT.md"


def test_required_artifact_stays_shared_outside_a_worker() -> None:
    required = RequiredArtifact(
        phase="development",
        artifact_type="development_result",
        artifact_path=".agent/artifacts/development_result.md",
        markdown_path=".agent/DEVELOPMENT_RESULT.md",
        normalizer=None,
    )

    assert required_artifact_for_invocation(required, None) is required
