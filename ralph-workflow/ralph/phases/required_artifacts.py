"""Centralized required-artifact metadata for all pipeline phases.

Artifact metadata is derived exclusively from ArtifactsPolicy via
resolve_required_artifact() and build_required_artifacts(). Artifact JSON
and markdown paths come from the contract fields declared in artifacts.toml
(artifact_json_path and markdown_summary_path). There are no built-in
override tables — all path overrides must be declared in artifacts.toml.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.development_result import normalize_development_result_content
from ralph.mcp.artifacts.typed_artifacts import (
    normalize_fix_result_content,
    normalize_issues_content,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.policy.models import ArtifactsPolicy

# Normalizers keyed by artifact_type — used by build_required_artifacts()
_ARTIFACT_TYPE_NORMALIZERS: dict[str, Callable[[dict[str, object]], dict[str, object]]] = {
    "development_result": normalize_development_result_content,
    "fix_result": normalize_fix_result_content,
    "issues": normalize_issues_content,
}


@dataclass(frozen=True)
class RequiredArtifact:
    """Metadata about an artifact contract for a pipeline phase.

    When artifact_required is False, an absent artifact does not fail the phase;
    a present artifact is still validated.
    """

    phase: str
    artifact_type: str
    json_path: str
    markdown_path: str | None
    normalizer: Callable[[dict[str, object]], dict[str, object]] | None
    artifact_required: bool = True


def build_required_artifacts(
    artifacts_policy: ArtifactsPolicy,
) -> dict[str, RequiredArtifact]:
    """Build a required-artifact registry from an ArtifactsPolicy.

    Derives the artifact table from the loaded artifacts.toml. Each artifact
    contract in the policy produces a RequiredArtifact entry keyed by drain name.
    JSON and markdown paths come from the policy contract fields (artifact_json_path
    and markdown_summary_path) with a conventional fallback for the JSON path.

    Args:
        artifacts_policy: Loaded artifacts policy.

    Returns:
        Dict mapping drain name -> RequiredArtifact for each artifact contract.
    """
    result: dict[str, RequiredArtifact] = {}
    for contract in artifacts_policy.artifacts.values():
        drain = str(contract.drain)
        artifact_type = contract.artifact_type

        # Use policy-declared paths; fall back to convention for json_path only.
        json_path = contract.artifact_json_path or f".agent/artifacts/{artifact_type}.json"
        markdown_path = contract.markdown_summary_path
        normalizer = _ARTIFACT_TYPE_NORMALIZERS.get(artifact_type)

        result[drain] = RequiredArtifact(
            phase=drain,
            artifact_type=artifact_type,
            json_path=json_path,
            markdown_path=markdown_path,
            normalizer=normalizer,
            artifact_required=contract.artifact_required,
        )
    return result


def resolve_required_artifact(
    artifacts_policy: ArtifactsPolicy,
    *,
    drain: str,
) -> RequiredArtifact | None:
    """Resolve the required artifact for a drain from the artifacts policy.

    Returns None when the drain has no artifact contract in the policy.
    Callers must declare artifacts in artifacts.toml — there is no built-in
    fallback registry.

    Args:
        artifacts_policy: Loaded artifacts policy.
        drain: The drain name to look up.

    Returns:
        RequiredArtifact if the drain has a contract, None otherwise.
    """
    try:
        registry = build_required_artifacts(artifacts_policy)
        return registry.get(drain)
    except AttributeError:
        return None


def retry_hint_path(phase: str) -> str:
    """Return the workspace-relative path for the retry hint file for a phase."""
    return f".agent/tmp/last_retry_error_{phase}.txt"


def build_retry_hint(
    phase: str,
    detail: str,
    *,
    registry: dict[str, RequiredArtifact] | None = None,
) -> str:
    """Build a retry hint message for a phase that failed to submit a required artifact.

    Args:
        phase: Pipeline phase name.
        detail: Error detail message.
        registry: Optional policy-derived artifact registry. When provided,
            the hint includes the specific artifact type and path.
    """
    ra = registry.get(phase) if registry is not None else None
    if ra is None:
        return (
            f"PREVIOUS ATTEMPT FAILED: The agent did not submit the required "
            f"artifact before declaring completion.\n\nDetails: {detail}"
        )
    return (
        f"PREVIOUS ATTEMPT FAILED: The agent did not submit the required "
        f"'{ra.artifact_type}' artifact at '{ra.json_path}' before declaring completion.\n\n"
        f"Details: {detail}"
    )


def build_missing_input_hint(phase: str, upstream_phase: str, artifact_path: str) -> str:
    """Build a retry hint for a phase that is missing a required upstream input artifact.

    Unlike build_retry_hint (which describes a missing *output*), this function
    describes a missing *input* — i.e., a handoff that a prior phase should have
    produced.  The hint is written to the phase's retry-hint file so the agent
    sees an explanation on the next attempt, but the message correctly names the
    upstream producer rather than blaming the current agent.
    """
    return (
        f"PIPELINE INPUT MISSING: The '{upstream_phase}' phase did not produce the "
        f"required artifact at '{artifact_path}'. This artifact is a required input "
        f"for the '{phase}' phase and must be present before '{phase}' can proceed. "
        f"The upstream handoff from '{upstream_phase}' must be completed first."
    )


__all__ = [
    "RequiredArtifact",
    "build_missing_input_hint",
    "build_required_artifacts",
    "build_retry_hint",
    "resolve_required_artifact",
    "retry_hint_path",
]
