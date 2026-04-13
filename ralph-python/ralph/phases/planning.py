"""Planning phase handler.

The planning phase invokes the planning agent to generate an implementation plan.
The agent submits a PlanningArtifact via MCP, which is then used to drive
the development phase.
"""

from __future__ import annotations

from loguru import logger

from ralph.phases import PhaseContext, register_handler
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event, PipelineEvent


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
        # After agent completes, the event handler will route to next phase
        return [PipelineEvent.AGENT_SUCCESS]

    return []
