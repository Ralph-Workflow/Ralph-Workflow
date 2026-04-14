"""Planning phase handler.

The planning phase invokes the planning agent to generate an implementation plan.
The agent submits a PlanningArtifact via MCP, which is then used to drive
the development phase.
"""

from __future__ import annotations

import json
from typing import cast

from loguru import logger

from ralph.phases import PhaseContext, register_handler
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event, PipelineEvent
from ralph.pipeline.work_units import WorkUnitsValidationError, parse_work_units_from_artifact
from ralph.policy.validation import PolicyValidationError, validate_work_units_against_policy


@register_handler("planning")
def handle_planning(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Handle the planning phase.

    Args:
        effect: The effect that triggered this phase.
        ctx: Phase context with workspace and policy.

    Returns:
        List of events to emit.
    """
    if isinstance(effect, PreparePromptEffect):
        # Prepare prompt and then invoke agent
        logger.info("Planning phase: preparing prompt")
        return [PipelineEvent.PROMPT_PREPARED]

    if isinstance(effect, InvokeAgentEffect):
        logger.info("Planning phase: invoking planning agent")
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
        # After agent completes, the event handler will route to next phase
        return [PipelineEvent.AGENT_SUCCESS]

    return []
