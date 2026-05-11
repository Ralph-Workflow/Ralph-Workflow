"""Centralized required-artifact metadata for all pipeline phases.

Artifact metadata is split across two policy surfaces. ``artifacts.toml`` owns
artifact type, JSON path, markdown handoff path, and schema normalizer lookup.
``pipeline.toml`` owns whether a phase's output artifact is required for
success. There are no built-in override tables — artifact paths must be
declared in ``artifacts.toml`` and requiredness must be declared on the phase
definition.
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

    from ralph.policy.models import ArtifactsPolicy, PipelinePolicy

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
    """Build a drain-keyed artifact registry from ArtifactsPolicy.

    The registry contains artifact identity and path metadata only. Callers that
    need phase-specific requiredness must use resolve_phase_required_artifact().
    """
    result: dict[str, RequiredArtifact] = {}
    for contract in artifacts_policy.artifacts.values():
        drain = str(contract.drain)
        artifact_type = contract.artifact_type
        json_path = contract.artifact_json_path or f".agent/artifacts/{artifact_type}.json"
        markdown_path = contract.markdown_summary_path
        normalizer = _ARTIFACT_TYPE_NORMALIZERS.get(artifact_type)

        result[drain] = RequiredArtifact(
            phase=drain,
            artifact_type=artifact_type,
            json_path=json_path,
            markdown_path=markdown_path,
            normalizer=normalizer,
            artifact_required=True,
        )
    return result


def resolve_required_artifact(
    artifacts_policy: ArtifactsPolicy,
    *,
    drain: str,
) -> RequiredArtifact | None:
    """Resolve artifact identity/path metadata for a drain from artifacts.toml."""
    try:
        registry = build_required_artifacts(artifacts_policy)
        return registry.get(drain)
    except AttributeError:
        return None


def resolve_phase_required_artifact(
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy,
    *,
    phase: str,
    drain: str | None = None,
) -> RequiredArtifact | None:
    """Resolve the artifact contract for a phase, including phase-owned requiredness."""
    phase_def = pipeline_policy.phases.get(phase)
    effective_drain = drain or (phase_def.drain if phase_def is not None else phase)
    ra = resolve_required_artifact(artifacts_policy, drain=effective_drain)
    if ra is None:
        return None
    required = phase_def.artifact_required if phase_def is not None else True
    return RequiredArtifact(
        phase=phase,
        artifact_type=ra.artifact_type,
        json_path=ra.json_path,
        markdown_path=ra.markdown_path,
        normalizer=ra.normalizer,
        artifact_required=required,
    )


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
    "resolve_phase_required_artifact",
    "resolve_required_artifact",
    "retry_hint_path",
]
