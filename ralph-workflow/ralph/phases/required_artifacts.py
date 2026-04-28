"""Centralized required-artifact metadata for all pipeline phases.

Every phase that demands a typed artifact as output is registered here.
Importing from this module (rather than duplicating string constants) is the
single source of truth for artifact paths and types.

The module-level REQUIRED_ARTIFACTS dict remains as the canonical built-in
registry. build_required_artifacts() derives a custom registry from an
ArtifactsPolicy for policy-driven artifact lookup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.development_result import normalize_development_result_content
from ralph.mcp.artifacts.plan import PLAN_ARTIFACT_PATH
from ralph.mcp.artifacts.typed_artifacts import (
    normalize_fix_result_content,
    normalize_issues_content,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.policy.models import ArtifactsPolicy

ISSUES_ARTIFACT_JSON_PATH = ".agent/artifacts/issues.json"
FIX_RESULT_ARTIFACT_JSON_PATH = ".agent/artifacts/fix_result.json"
DEV_ANALYSIS_DECISION_JSON_PATH = ".agent/artifacts/development_analysis_decision.json"
REVIEW_ANALYSIS_DECISION_JSON_PATH = ".agent/artifacts/review_analysis_decision.json"
DEV_RESULT_ARTIFACT_JSON_PATH = ".agent/artifacts/development_result.json"

_PLAN_ARTIFACT_JSON_PATH = PLAN_ARTIFACT_PATH

# Normalizers keyed by artifact_type — used by build_required_artifacts()
_ARTIFACT_TYPE_NORMALIZERS: dict[str, Callable[[dict[str, object]], dict[str, object]]] = {
    "development_result": normalize_development_result_content,
    "fix_result": normalize_fix_result_content,
    "issues": normalize_issues_content,
}

# Markdown path overrides keyed by artifact_type
_ARTIFACT_TYPE_MARKDOWN_PATHS: dict[str, str] = {
    "plan": ".agent/PLAN.md",
    "development_result": ".agent/DEVELOPMENT_RESULT.md",
}


@dataclass(frozen=True)
class RequiredArtifact:
    """Metadata about an artifact required by a pipeline phase."""

    phase: str
    artifact_type: str
    json_path: str
    markdown_path: str | None
    normalizer: Callable[[dict[str, object]], dict[str, object]] | None


REQUIRED_ARTIFACTS: dict[str, RequiredArtifact] = {
    "planning": RequiredArtifact(
        phase="planning",
        artifact_type="plan",
        json_path=_PLAN_ARTIFACT_JSON_PATH,
        markdown_path=".agent/PLAN.md",
        normalizer=None,
    ),
    "development": RequiredArtifact(
        phase="development",
        artifact_type="development_result",
        json_path=DEV_RESULT_ARTIFACT_JSON_PATH,
        markdown_path=".agent/DEVELOPMENT_RESULT.md",
        normalizer=normalize_development_result_content,
    ),
    "development_analysis": RequiredArtifact(
        phase="development_analysis",
        artifact_type="development_analysis_decision",
        json_path=DEV_ANALYSIS_DECISION_JSON_PATH,
        markdown_path=None,
        normalizer=None,
    ),
    "review": RequiredArtifact(
        phase="review",
        artifact_type="issues",
        json_path=ISSUES_ARTIFACT_JSON_PATH,
        markdown_path=None,
        normalizer=normalize_issues_content,
    ),
    "review_analysis": RequiredArtifact(
        phase="review_analysis",
        artifact_type="review_analysis_decision",
        json_path=REVIEW_ANALYSIS_DECISION_JSON_PATH,
        markdown_path=None,
        normalizer=None,
    ),
    "fix": RequiredArtifact(
        phase="fix",
        artifact_type="fix_result",
        json_path=FIX_RESULT_ARTIFACT_JSON_PATH,
        markdown_path=None,
        normalizer=normalize_fix_result_content,
    ),
}


def build_required_artifacts(
    artifacts_policy: ArtifactsPolicy,
) -> dict[str, RequiredArtifact]:
    """Build a required-artifact registry from an ArtifactsPolicy.

    Derives the artifact table from the loaded artifacts.toml. Each artifact
    contract in the policy produces a RequiredArtifact entry keyed by drain name.
    Normalizers and markdown paths are resolved from the built-in registries.

    Args:
        artifacts_policy: Loaded artifacts policy.

    Returns:
        Dict mapping drain name -> RequiredArtifact for each artifact contract.
    """
    result: dict[str, RequiredArtifact] = {}
    for contract in artifacts_policy.artifacts.values():
        drain = str(contract.drain)
        artifact_type = contract.artifact_type

        # Build the JSON path based on artifact type convention
        json_path = _artifact_type_to_json_path(artifact_type)
        markdown_path = _ARTIFACT_TYPE_MARKDOWN_PATHS.get(artifact_type)
        normalizer = _ARTIFACT_TYPE_NORMALIZERS.get(artifact_type)

        result[drain] = RequiredArtifact(
            phase=drain,
            artifact_type=artifact_type,
            json_path=json_path,
            markdown_path=markdown_path,
            normalizer=normalizer,
        )
    return result


def _artifact_type_to_json_path(artifact_type: str) -> str:
    """Convert artifact_type to its conventional JSON path.

    Uses the same path conventions as the built-in REQUIRED_ARTIFACTS dict.
    Falls back to a derived path for unknown artifact types.
    """
    # Check built-in artifacts for an exact match
    for ra in REQUIRED_ARTIFACTS.values():
        if ra.artifact_type == artifact_type:
            return ra.json_path
    # Derive path for unknown artifact types
    return f".agent/artifacts/{artifact_type}.json"


def retry_hint_path(phase: str) -> str:
    """Return the workspace-relative path for the retry hint file for a phase."""
    return f".agent/tmp/last_retry_error_{phase}.txt"


def build_retry_hint(phase: str, detail: str) -> str:
    """Build a retry hint message for a phase that failed to submit a required artifact."""
    ra = REQUIRED_ARTIFACTS.get(phase)
    if ra is None:
        return detail
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
    "DEV_ANALYSIS_DECISION_JSON_PATH",
    "DEV_RESULT_ARTIFACT_JSON_PATH",
    "FIX_RESULT_ARTIFACT_JSON_PATH",
    "ISSUES_ARTIFACT_JSON_PATH",
    "REQUIRED_ARTIFACTS",
    "REVIEW_ANALYSIS_DECISION_JSON_PATH",
    "RequiredArtifact",
    "build_missing_input_hint",
    "build_required_artifacts",
    "build_retry_hint",
    "retry_hint_path",
]
