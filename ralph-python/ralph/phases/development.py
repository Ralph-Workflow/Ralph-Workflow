"""Development phase handler.

The development phase invokes the development agent to implement code changes
based on the planning artifact. It may embed an analysis step that decides
whether to continue development or loop back for more iterations.
"""

from __future__ import annotations

import json
from typing import cast

from loguru import logger

from ralph.config.enums import AnalysisDecision
from ralph.phases import PhaseContext, register_handler
from ralph.phases.analysis import parse_analysis_decision
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event, PipelineEvent
from ralph.pipeline.work_units import WorkUnitsValidationError, parse_work_units_from_artifact
from ralph.policy.validation import PolicyValidationError, validate_work_units_against_policy


@register_handler("development")
def handle_development(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Handle the development phase.

    Args:
        effect: The effect that triggered this phase.
        ctx: Phase context with workspace and policy.

    Returns:
        List of events to emit.
    """
    if isinstance(effect, PreparePromptEffect):
        logger.info(
            "Development phase: preparing prompt (iteration={})",
            effect.iteration,
        )
        return [PipelineEvent.PROMPT_PREPARED]

    if isinstance(effect, InvokeAgentEffect):
        logger.info("Development phase: invoking development agent")
        planning_artifact_path = ".agent/artifacts/planning.json"
        if ctx.workspace.exists(planning_artifact_path):
            try:
                artifact_obj: object = json.loads(ctx.workspace.read(planning_artifact_path))
                if isinstance(artifact_obj, dict):
                    artifact = cast("dict[str, object]", artifact_obj)
                    parsed = parse_work_units_from_artifact(artifact)
                    if parsed is not None:
                        validate_work_units_against_policy(parsed, ctx.pipeline_policy)
            except (json.JSONDecodeError, WorkUnitsValidationError, PolicyValidationError) as exc:
                logger.warning("Invalid planning work_units artifact: {}", exc)
                return [PipelineEvent.FAILED]
        return [PipelineEvent.AGENT_SUCCESS]

    return []


@register_handler("development_analysis")
def handle_development_analysis(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Handle the development analysis step (embedded in development phase).

    After the development agent completes, the analysis step reads the
    decision from the development analysis artifact and routes accordingly.

    Args:
        effect: The effect that triggered this phase.
        ctx: Phase context with workspace and policy.

    Returns:
        List of events to emit.
    """
    if isinstance(effect, InvokeAgentEffect):
        # Read the analysis artifact to determine routing
        decision = parse_analysis_decision(ctx, "development_analysis")
        logger.info("Development analysis decision: {}", decision)

        if decision in (AnalysisDecision.PROCEED, AnalysisDecision.COMPLETE):
            return [PipelineEvent.ANALYSIS_SUCCESS]
        elif decision == AnalysisDecision.REVISE:
            return [PipelineEvent.ANALYSIS_LOOPBACK]
        elif decision in (AnalysisDecision.FAILURE, AnalysisDecision.ESCALATE):
            logger.warning("Analysis decision {} triggers pipeline failure", decision)
            return [PipelineEvent.FAILED]
        else:
            logger.warning("Unknown analysis decision: {}, defaulting to success", decision)
            return [PipelineEvent.ANALYSIS_SUCCESS]

    return []
