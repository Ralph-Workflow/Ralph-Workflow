"""Helpers for reading persisted MCP artifacts inside phase handlers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.policy.models import ArtifactContract
    from ralph.workspace.protocol import Workspace


class PhaseArtifactError(ValueError):
    """Raised when a phase artifact is missing or malformed."""


def load_phase_artifact(workspace: Workspace, path: str) -> dict[str, object]:
    """Load a persisted MCP artifact wrapper from the workspace."""
    raw = json.loads(workspace.read(path))
    if not isinstance(raw, dict):
        raise PhaseArtifactError(f"Artifact at {path} must be a JSON object")
    return cast("dict[str, object]", raw)


def unwrap_phase_artifact_content(
    artifact: Mapping[str, object],
    *,
    expected_type: str | None = None,
) -> dict[str, object]:
    """Return the inner content payload from a persisted artifact wrapper."""
    artifact_type = artifact.get("type")
    if expected_type is not None and artifact_type != expected_type:
        raise PhaseArtifactError(
            f"Artifact type mismatch: expected {expected_type}, got {artifact_type!r}"
        )

    content = artifact.get("content")
    if not isinstance(content, dict):
        raise PhaseArtifactError("Artifact content must be a JSON object")
    return cast("dict[str, object]", content)


def artifact_contract_for_drain(
    artifacts_policy: object,
    drain: str,
    artifact_type: str,
) -> ArtifactContract | None:
    """Find the artifact contract for a drain/type pair if one exists."""
    artifacts = getattr(artifacts_policy, "artifacts", None)
    if not isinstance(artifacts, dict):
        return None

    for contract in artifacts.values():
        if (
            getattr(contract, "drain", None) == drain
            and getattr(contract, "artifact_type", None) == artifact_type
        ):
            return cast("ArtifactContract", contract)
    return None


def decision_vocabulary_for_drain(
    artifacts_policy: object,
    drain: str,
    artifact_type: str,
) -> list[str]:
    contract = artifact_contract_for_drain(artifacts_policy, drain, artifact_type)
    vocabulary = getattr(contract, "decision_vocabulary", []) if contract is not None else []
    return list(vocabulary) if isinstance(vocabulary, list) else []
