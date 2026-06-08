"""Helpers for reading persisted MCP artifacts inside phase handlers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.pipeline.events import PhaseFailureEvent
from ralph.recovery.classifier import FailureCategory

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.policy.models import ArtifactContract
    from ralph.workspace.protocol import Workspace


class PhaseArtifactError(ValueError):
    """Raised when a phase artifact is missing or malformed."""


def load_phase_artifact(workspace: Workspace, path: str) -> dict[str, object]:
    """Load a persisted MCP artifact wrapper from the workspace."""
    try:
        content = workspace.read(path)
    except (FileNotFoundError, OSError) as exc:
        raise PhaseArtifactError(f"Artifact not found at {path}") from exc

    try:
        raw_obj: object = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise PhaseArtifactError(f"Artifact at {path} must be valid JSON text") from exc
    if not isinstance(raw_obj, dict):
        raise PhaseArtifactError(f"Artifact at {path} must be a JSON object")
    return cast("dict[str, object]", raw_obj)


def unwrap_phase_artifact_content(
    artifact: Mapping[str, object],
    *,
    expected_type: str | None = None,
) -> dict[str, object]:
    """Return the inner content payload from a persisted artifact wrapper."""
    artifact_type = artifact.get("type")
    if expected_type is not None and artifact_type is not None and artifact_type != expected_type:
        raise PhaseArtifactError(
            f"Artifact type mismatch: expected {expected_type}, got {artifact_type!r}"
        )

    content = artifact.get("content")
    if content is None and artifact_type is None:
        return dict(artifact)
    if not isinstance(content, dict):
        raise PhaseArtifactError("Artifact content must be a JSON object")
    return cast("dict[str, object]", content)


def validate_artifact_on_disk(
    workspace: Workspace,
    required_artifact: RequiredArtifact,
) -> str | None:
    """Return None if the required artifact is present, parseable, and valid.

    Otherwise return a human-readable failure detail. This is the SINGLE on-disk
    artifact-contract check used by both the pipeline phase gates and the commit
    command, so "missing / can't parse / wrong type / wrong format" detection
    cannot drift between callers.
    """
    try:
        artifact = load_phase_artifact(workspace, required_artifact.json_path)
        content = unwrap_phase_artifact_content(
            artifact, expected_type=required_artifact.artifact_type
        )
    except PhaseArtifactError as exc:
        return str(exc)

    if required_artifact.normalizer is not None:
        try:
            required_artifact.normalizer(content)
        except ValueError as exc:
            return f"Artifact at {required_artifact.json_path} failed validation: {exc}"
    return None


def artifact_validation_failure_event(
    phase: str,
    reason: str,
    *,
    retry_in_session: bool = True,
) -> PhaseFailureEvent:
    """Build a typed phase failure event for artifact/proof validation issues."""
    return PhaseFailureEvent(
        phase=phase,
        reason=reason,
        recoverable=True,
        retry_in_session=retry_in_session,
        failure_category=FailureCategory.ARTIFACT_VALIDATION,
    )


def artifact_contract_for_drain(
    artifacts_policy: object,
    drain: str,
    artifact_type: str,
) -> ArtifactContract | None:
    """Find the artifact contract for a drain/type pair if one exists."""
    raw_artifacts: object = getattr(artifacts_policy, "artifacts", None)
    if not isinstance(raw_artifacts, dict):
        return None

    artifacts = cast("dict[str, object]", raw_artifacts)

    for contract in artifacts.values():
        contract_drain = cast("object", getattr(contract, "drain", None))
        contract_artifact_type = cast("object", getattr(contract, "artifact_type", None))
        if (
            isinstance(contract_drain, str)
            and isinstance(contract_artifact_type, str)
            and contract_drain == drain
            and contract_artifact_type == artifact_type
        ):
            return cast("ArtifactContract", contract)
    return None


def decision_vocabulary_for_drain(
    artifacts_policy: object,
    drain: str,
    artifact_type: str,
) -> list[str]:
    """Return the allowed decision strings for a given drain and artifact type."""
    contract = artifact_contract_for_drain(artifacts_policy, drain, artifact_type)
    vocabulary: object = (
        getattr(contract, "decision_vocabulary", []) if contract is not None else []
    )
    return list(vocabulary) if isinstance(vocabulary, list) else []
