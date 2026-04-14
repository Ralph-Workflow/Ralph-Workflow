"""Shared analysis logic for parsing analysis decisions.

The analysis phase reads a typed artifact submitted by the agent via MCP
and extracts the decision field to route the pipeline.

Analysis decisions are explicit typed enums (AnalysisDecision) that drive
the reducer routing for development_analysis and review_analysis phases.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, TypedDict, cast

from loguru import logger

from ralph.config.enums import AnalysisDecision
from ralph.phases import get_handler
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect


class AnalysisArtifact(TypedDict, total=False):
    status: str
    decision: str


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
    artifact_path = f".agent/artifacts/{drain_name}.json"

    if not ctx.workspace.exists(artifact_path):
        logger.warning(
            "No analysis artifact found at {}. Defaulting to PROCEED.",
            artifact_path,
        )
        return AnalysisDecision.PROCEED

    try:
        content = ctx.workspace.read(artifact_path)
        artifact = cast("AnalysisArtifact", json.loads(content))

        # MCP artifact status field: completed, partial, failed
        # Also support legacy "decision" field for backward compatibility
        status = artifact.get("status") or artifact.get("decision", "completed")
        status_str = str(status).lower()

        # Map status to AnalysisDecision
        decision = _map_status_to_decision(status_str)

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
        return AnalysisDecision.PROCEED


def _map_status_to_decision(status: str) -> AnalysisDecision:
    """Map MCP artifact status string to AnalysisDecision enum.

    Args:
        status: Status string from MCP artifact.

    Returns:
        Corresponding AnalysisDecision enum value.
    """
    # Map completed/proceed/success to PROCEED
    if status in ("completed", "proceed", "success", "approve", "approved"):
        return AnalysisDecision.PROCEED

    # Map partial/revise/changes to REVISE
    if status in ("partial", "revise", "changes", "request_changes", "needs_work"):
        return AnalysisDecision.REVISE

    # Map escalate to ESCALATE
    if status in ("escalate", "escalation"):
        return AnalysisDecision.ESCALATE

    # Map failed/failure to FAILURE
    if status in ("failed", "failure", "error"):
        return AnalysisDecision.FAILURE

    # Default to COMPLETE for unknown statuses
    return AnalysisDecision.COMPLETE


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
