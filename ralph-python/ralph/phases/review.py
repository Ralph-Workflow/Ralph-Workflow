"""Review phase handler.

The review phase invokes the review agent to review code changes.
It may embed an analysis step that decides whether to approve the changes
or request fixes.
"""

from __future__ import annotations

from loguru import logger

from ralph.phases import PhaseContext, register_handler
from ralph.phases.analysis import parse_analysis_decision
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event, PipelineEvent


@register_handler("review")
def handle_review(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Handle the review phase.

    Args:
        effect: The effect that triggered this phase.
        ctx: Phase context with workspace and policy.

    Returns:
        List of events to emit.
    """
    if isinstance(effect, PreparePromptEffect):
        logger.info(
            "Review phase: preparing prompt (pass={})",
            effect.iteration,
        )
        return [PipelineEvent.PROMPT_PREPARED]

    if isinstance(effect, InvokeAgentEffect):
        logger.info("Review phase: invoking review agent")
        return [PipelineEvent.AGENT_SUCCESS]

    return []


@register_handler("review_analysis")
def handle_review_analysis(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Handle the review analysis step.

    After the review agent completes, reads the review analysis artifact
    to determine routing (approve or request changes).

    Args:
        effect: The effect that triggered this phase.
        ctx: Phase context with workspace and policy.

    Returns:
        List of events to emit.
    """
    if isinstance(effect, InvokeAgentEffect):
        decision = parse_analysis_decision(ctx, "review_analysis")
        logger.info("Review analysis decision: {}", decision)

        if decision in ("approve", "success", "continue"):
            return [PipelineEvent.ANALYSIS_SUCCESS]
        elif decision in ("request_changes", "loopback", "retry"):
            return [PipelineEvent.ANALYSIS_LOOPBACK]
        elif decision in ("fail", "reject"):
            return [PipelineEvent.ANALYSIS_SUCCESS]
        else:
            logger.warning(
                "Unknown review analysis decision: {}. Defaulting to success.",
                decision,
            )
            return [PipelineEvent.ANALYSIS_SUCCESS]

    return []
