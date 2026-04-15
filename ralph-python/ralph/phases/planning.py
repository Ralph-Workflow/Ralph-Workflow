"""Planning phase handler.

The planning phase invokes the planning agent to generate an implementation plan.
The agent submits a PlanningArtifact via MCP, which is then used to drive
the development phase.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from loguru import logger

from ralph.mcp.plan_artifact import (
    PLAN_ARTIFACT_PATH,
    PLAN_DRAFT_PATH,
    PlanArtifactValidationError,
    load_plan_draft,
    normalize_plan_artifact_content,
)
from ralph.phases import PhaseContext, register_handler
from ralph.phases.artifacts import load_phase_artifact, unwrap_phase_artifact_content
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
        if _should_clear_plan_draft(ctx):
            logger.info("Clearing stale plan draft at {}", PLAN_DRAFT_PATH)
            ctx.workspace.remove(PLAN_DRAFT_PATH)
        return [PipelineEvent.PROMPT_PREPARED]

    if isinstance(effect, InvokeAgentEffect):
        logger.info("Planning phase: invoking planning agent")
        planning_artifact_path = PLAN_ARTIFACT_PATH
        if not ctx.workspace.exists(planning_artifact_path):
            logger.warning("Planning agent completed without producing {}", planning_artifact_path)
            return [PipelineEvent.AGENT_FAILURE]

        try:
            artifact_wrapper = load_phase_artifact(ctx.workspace, planning_artifact_path)
            artifact = normalize_plan_artifact_content(
                unwrap_phase_artifact_content(artifact_wrapper, expected_type="plan")
            )
            parsed = parse_work_units_from_artifact(artifact)
            if parsed is not None:
                validate_work_units_against_policy(parsed, ctx.pipeline_policy)
        except (
            json.JSONDecodeError,
            PlanArtifactValidationError,
            ValueError,
            WorkUnitsValidationError,
            PolicyValidationError,
        ) as exc:
            logger.warning("Invalid planning artifact: {}", exc)
            return [PipelineEvent.AGENT_FAILURE]
        # After agent completes, the event handler will route to next phase
        return [PipelineEvent.AGENT_SUCCESS]

    return []


def _should_clear_plan_draft(ctx: PhaseContext) -> bool:
    if not ctx.workspace.exists(PLAN_DRAFT_PATH):
        return False
    if not ctx.workspace.exists(PLAN_ARTIFACT_PATH):
        return False

    artifact_dir = Path(ctx.workspace.absolute_path(".agent/artifacts"))
    draft = load_plan_draft(artifact_dir)
    if draft is None:
        return False

    updated_at = draft.get("updated_at")
    if not isinstance(updated_at, str):
        return False

    try:
        draft_updated_at = datetime.fromisoformat(updated_at).timestamp()
    except ValueError:
        return False

    plan_mtime = Path(ctx.workspace.absolute_path(PLAN_ARTIFACT_PATH)).stat().st_mtime
    return draft_updated_at <= plan_mtime
