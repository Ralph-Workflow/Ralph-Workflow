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
from ralph.phases import get_handler
from ralph.phases.artifacts import (
    decision_vocabulary_for_drain,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect

if TYPE_CHECKING:
    from ralph.phases import PhaseContext
    from ralph.pipeline.events import Event


def parse_analysis_decision(
    ctx: PhaseContext,
    drain_name: str,
) -> AnalysisDecision:
    """Parse the analysis decision from the MCP artifact.

    Reads the artifact file from the workspace and extracts the decision field,
    which is mapped to an AnalysisDecision enum value.

    Args:
        ctx: Phase context with workspace.
        drain_name: Name of the drain (development_analysis or review_analysis).

    Returns:
        AnalysisDecision enum value. Defaults to PROCEED if parsing fails.
    """
    artifact_type = f"{drain_name}_decision"
    artifact_path = f".agent/artifacts/{artifact_type}.json"

    if not ctx.workspace.exists(artifact_path):
        logger.warning(
            "No analysis artifact found at {}. Defaulting to PROCEED.",
            artifact_path,
        )
        return AnalysisDecision.PROCEED

    try:
        artifact = load_phase_artifact(ctx.workspace, artifact_path)
        content = unwrap_phase_artifact_content(artifact, expected_type=artifact_type)

        # MCP artifact status field: completed, partial, failed
        # Also support legacy "decision" field for backward compatibility
        status = content.get("status") or content.get("decision", "completed")
        status_str = str(status).lower()

        # Map status to AnalysisDecision
        decision = _map_status_to_decision(status_str)
        vocabulary = decision_vocabulary_for_drain(ctx.artifacts_policy, drain_name, artifact_type)
        if vocabulary and status_str not in vocabulary:
            logger.warning(
                "Analysis artifact at {} used status '{}' outside allowed vocabulary {}.",
                artifact_path,
                status_str,
                vocabulary,
            )
            return AnalysisDecision.FAILURE

        logger.debug(
            "Parsed analysis decision: {} (status={}) from {}",
            decision,
            status_str,
            artifact_path,
        )
        return decision
    except Exception as exc:
        logger.warning(
            "Failed to parse analysis artifact at {}: {}. Defaulting to PROCEED.",
            artifact_path,
            exc,
        )
        return AnalysisDecision.FAILURE


def _map_status_to_decision(status: str) -> AnalysisDecision:
    """Map MCP artifact status string to AnalysisDecision enum.

    Args:
        status: Status string from MCP artifact.

    Returns:
        Corresponding AnalysisDecision enum value.
    """
    # Map completed/proceed/success to PROCEED
    if status in ("completed", "proceed", "success", "continue", "approve", "approved"):
        return AnalysisDecision.PROCEED

    # Map partial/revise/changes to REVISE
    if status in (
        "partial",
        "revise",
        "changes",
        "request_changes",
        "needs_work",
        "loopback",
        "retry",
    ):
        return AnalysisDecision.REVISE

    # Map escalate to ESCALATE
    if status in ("escalate", "escalation"):
        return AnalysisDecision.ESCALATE

    # Map failed/failure to FAILURE
    if status in ("failed", "failure", "error", "fail", "reject"):
        return AnalysisDecision.FAILURE

    # Default to FAILURE for unknown statuses
    return AnalysisDecision.FAILURE


def validate_decision_vocabulary(
    decision: AnalysisDecision,
    vocabulary: list[str],
) -> bool:
    """Validate that a decision is in the allowed vocabulary.

    Args:
        decision: The AnalysisDecision to validate.
        vocabulary: List of allowed decision values as strings.

    Returns:
        True if the decision is in the vocabulary.
    """
    if not vocabulary:
        return True  # Empty vocabulary means any decision is allowed
    return decision.value in vocabulary


def handle_analysis(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Compatibility wrapper for analysis handling.

    Dispatches to the concrete analysis handlers used by the development and
    review analysis phases.
    """
    if isinstance(effect, (InvokeAgentEffect, PreparePromptEffect)) and effect.phase in (
        "development_analysis",
        "review_analysis",
    ):
        return get_handler(effect.phase)(effect, ctx)
    return []
