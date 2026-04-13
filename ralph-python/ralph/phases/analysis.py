"""Shared analysis logic for parsing analysis decisions.

The analysis phase reads a typed artifact submitted by the agent via MCP
and extracts the decision field to route the pipeline.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from ralph.phases import PhaseContext


def parse_analysis_decision(
    ctx: PhaseContext,
    drain_name: str,
) -> str:
    """Parse the analysis decision from the MCP artifact.

    Reads the artifact file from the workspace and extracts the decision field.

    Args:
        ctx: Phase context with workspace.
        drain_name: Name of the drain (development_analysis or review_analysis).

    Returns:
        Decision string (continue, success, loopback, fail, etc.).
    """
    artifact_path = f".agent/artifacts/{drain_name}.json"

    if not ctx.workspace.exists(artifact_path):
        logger.warning(
            "No analysis artifact found at {}. Defaulting to success.",
            artifact_path,
        )
        return "success"

    try:
        content = ctx.workspace.read(artifact_path)
        artifact = json.loads(content)
        decision = str(artifact.get("decision", "success"))
        logger.debug("Parsed analysis decision: {} from {}", decision, artifact_path)
        return decision
    except Exception as exc:
        logger.warning(
            "Failed to parse analysis artifact at {}: {}. Defaulting to success.",
            artifact_path,
            exc,
        )
        return "success"


def validate_decision_vocabulary(
    decision: str,
    vocabulary: list[str],
) -> bool:
    """Validate that a decision is in the allowed vocabulary.

    Args:
        decision: The decision string to validate.
        vocabulary: List of allowed decision values.

    Returns:
        True if the decision is in the vocabulary.
    """
    if not vocabulary:
        return True  # Empty vocabulary means any decision is allowed
    return decision in vocabulary
