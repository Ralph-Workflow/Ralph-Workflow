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
from ralph.recovery.retry_prompt import build_retry_error_block

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
    prior_output: list[str] | None = None,
    submit_tool_name: str | None = None,
    example_payload: str | None = None,
) -> str:
    """Build the retry hint for an agent that failed to submit a required artifact.

    This is the SINGLE source of artifact-missing retry guidance — every caller
    (pipeline phase gates AND the commit command) routes through it, so the
    recovery cannot drift. When ``prior_output``/``submit_tool_name`` are given,
    the hint additionally echoes the agent's own prior analysis back and tells it
    to submit via the named tool, so a model that already drafted the artifact
    submits it instead of restarting.

    Args:
        phase: Pipeline phase / drain name.
        detail: Error detail message.
        registry: Optional artifact registry; when provided the hint names the
            specific artifact type and json path.
        prior_output: The agent's prior output lines, echoed back as context.
        submit_tool_name: The submit-artifact tool the agent must call.
        example_payload: An example submit-tool arguments payload, if available.
    """
    ra = registry.get(phase) if registry is not None else None
    if ra is None:
        block = build_retry_error_block(
            failure_summary=(
                "the required artifact was not submitted before completion was declared"
            ),
            detail=detail,
        )
        artifact_type, json_path = "the required artifact", None
    else:
        block = build_retry_error_block(
            failure_summary=(
                f"required artifact '{ra.artifact_type}' at '{ra.json_path}' "
                "was not submitted or was invalid"
            ),
            detail=detail,
        )
        artifact_type, json_path = ra.artifact_type, ra.json_path

    lines = [block, "", "Submit the artifact now. Do not restart the task from scratch."]
    tool = submit_tool_name or "the submit-artifact MCP tool"
    lines.append(
        f'Call {tool} with artifact_type="{artifact_type}" and put the payload in '
        "the content field as a JSON string."
    )
    if example_payload:
        lines.append(f"Example MCP arguments: {example_payload}")
    if json_path is not None:
        lines.append(
            f"If the submit-artifact MCP tool is unavailable, write the raw payload "
            f"JSON to {json_path} instead. Do not use content_path for this retry."
        )
    if prior_output:
        echoed = "\n".join(prior_output[-12:])
        lines.append("Your prior analysis (submit it, do not redo it):")
        lines.append(echoed)
    return "\n".join(lines)


def build_missing_input_hint(phase: str, upstream_phase: str, artifact_path: str) -> str:
    """Build a retry hint for a phase that is missing a required upstream input artifact.

    Unlike build_retry_hint (which describes a missing *output*), this function
    describes a missing *input* — i.e., a handoff that a prior phase should have
    produced. The hint is written to the phase's retry-hint file so the agent
    sees an explanation on the next attempt, but the message correctly names the
    upstream producer rather than blaming the current agent.
    """
    return (
        f"PIPELINE INPUT MISSING: The '{upstream_phase}' phase did not produce the "
        f"required artifact at '{artifact_path}'. This artifact is a required input "
        f"for the '{phase}' phase and must be present before '{phase}' can proceed. "
        f"The upstream handoff from '{upstream_phase}' must be completed first."
    )


def build_proof_failure_hint(phase: str, detail: str) -> str:
    """Build a retry hint for a phase that submitted proof but failed validation."""
    return build_retry_error_block(
        failure_summary="proof entries are incomplete or invalid",
        detail=detail,
    )


__all__ = [
    "RequiredArtifact",
    "build_missing_input_hint",
    "build_proof_failure_hint",
    "build_required_artifacts",
    "build_retry_hint",
    "resolve_phase_required_artifact",
    "resolve_required_artifact",
    "retry_hint_path",
]
