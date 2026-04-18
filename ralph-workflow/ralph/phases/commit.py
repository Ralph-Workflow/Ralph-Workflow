"""Commit phase handler.

The commit phases (development_commit and review_commit) handle git operations
after successful development or review phases. They stage and commit changes
with an appropriate message.
"""

from __future__ import annotations

from loguru import logger

from ralph.phases import PhaseContext, register_handler
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event, PipelineEvent


@register_handler("development_commit")
def handle_development_commit(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Handle the development commit phase.

    Stages and commits changes after successful development.

    Args:
        effect: The effect that triggered this phase.
        ctx: Phase context with workspace and policy.

    Returns:
        List of events to emit.
    """
    if isinstance(effect, InvokeAgentEffect):
        logger.info("Development commit: committing changes")
        # Commit is automatic after successful development
        # The reducer handles git operations based on this event
        return [PipelineEvent.ANALYSIS_SUCCESS]

    return []


@register_handler("review_commit")
def handle_review_commit(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Handle the review commit phase.

    Stages and commits changes after successful review approval.

    Args:
        effect: The effect that triggered this phase.
        ctx: Phase context with workspace and policy.

    Returns:
        List of events to emit.
    """
    if isinstance(effect, InvokeAgentEffect):
        logger.info("Review commit: committing approved changes")
        # Final commit after review approval
        return [PipelineEvent.ANALYSIS_SUCCESS]

    return []


def handle_commit(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Compatibility wrapper for commit handling.

    Dispatches to the concrete commit handlers for development/review phases.
    """
    if isinstance(effect, (InvokeAgentEffect, PreparePromptEffect)):
        if effect.phase == "development_commit":
            return handle_development_commit(effect, ctx)
        if effect.phase == "review_commit":
            return handle_review_commit(effect, ctx)
    return []
