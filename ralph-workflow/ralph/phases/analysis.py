"""Shared analysis logic for parsing analysis decisions.

The analysis phase reads a typed artifact submitted by the agent via MCP
and extracts the decision field to route the pipeline.

Analysis decisions are explicit typed enums (AnalysisDecision) that drive
the reducer routing for development_analysis and review_analysis phases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.config.enums import AnalysisDecision
from ralph.phases.artifacts import (
    decision_vocabulary_for_drain,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)

if TYPE_CHECKING:
    from ralph.phases import PhaseContext

_STATUS_TO_DECISION: dict[str, AnalysisDecision] = {
    "completed": AnalysisDecision.PROCEED,
    "request_changes": AnalysisDecision.REVISE,
    "failed": AnalysisDecision.FAILURE,
}


def parse_analysis_decision(
    ctx: PhaseContext,
    drain_name: str,
) -> AnalysisDecision:
    """Parse the analysis decision from the MCP artifact.

    Reads the artifact file from the workspace and extracts the status field,
    which is mapped to an AnalysisDecision enum value using the policy vocabulary.

    Args:
        ctx: Phase context with workspace.
        drain_name: Name of the drain (development_analysis or review_analysis).

    Returns:
        AnalysisDecision enum value. Defaults to FAILURE if parsing fails.
    """
    artifact_type = f"{drain_name}_decision"
    artifact_path = f".agent/artifacts/{artifact_type}.json"

    if not ctx.workspace.exists(artifact_path):
        logger.warning(
            "No analysis artifact found at {}. Defaulting to FAILURE.",
            artifact_path,
        )
        return AnalysisDecision.FAILURE

    try:
        artifact = load_phase_artifact(ctx.workspace, artifact_path)
        content = unwrap_phase_artifact_content(artifact, expected_type=artifact_type)
        status = str(content.get("status", "")).lower()

        vocabulary = decision_vocabulary_for_drain(ctx.artifacts_policy, drain_name, artifact_type)
        if vocabulary and status not in vocabulary:
            logger.warning(
                "Analysis artifact at {} used status '{}' outside allowed vocabulary {}.",
                artifact_path,
                status,
                vocabulary,
            )
            return AnalysisDecision.FAILURE

        decision = _STATUS_TO_DECISION.get(status)
        if decision is None:
            logger.warning(
                "Unknown status '{}' in analysis artifact at {}. Defaulting to FAILURE.",
                status,
                artifact_path,
            )
            return AnalysisDecision.FAILURE

        logger.debug(
            "Parsed analysis decision: {} (status={}) from {}",
            decision,
            status,
            artifact_path,
        )
        return decision
    except Exception as exc:
        logger.warning(
            "Failed to parse analysis artifact at {}: {}. Defaulting to FAILURE.",
            artifact_path,
            exc,
        )
        return AnalysisDecision.FAILURE
