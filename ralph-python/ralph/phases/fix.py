"""Fix phase handler.

The fix phase is invoked when review_analysis signals a loopback (request_changes).
It applies agent-suggested fixes to the codebase.
"""

from __future__ import annotations

from loguru import logger

from ralph.phases import PhaseContext, register_handler
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event, PipelineEvent


@register_handler("fix")
def handle_fix(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Handle the fix phase.

    Args:
        effect: The effect that triggered this phase.
        ctx: Phase context with workspace and policy.

    Returns:
        List of events to emit.
    """
    if isinstance(effect, PreparePromptEffect):
        logger.info(
            "Fix phase: preparing prompt (iteration={})",
            effect.iteration,
        )
        return [PipelineEvent.PROMPT_PREPARED]

    if isinstance(effect, InvokeAgentEffect):
        logger.info("Fix phase: invoking fix agent")
        return [PipelineEvent.AGENT_SUCCESS]

    return []
