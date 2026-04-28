"""Commit phase handler.

Commit-role phases handle git operations after successful development or review
phases. They stage and commit changes with an appropriate message.

If the working tree has no uncommitted changes when a commit phase is entered,
the handler emits ``COMMIT_SKIPPED`` so the reducer can advance routing without
incrementing iteration/reviewer_pass counters for a no-op pass.

The generic handle_commit_phase() function works for any phase with role='commit'.
It is registered via register_role_handlers(policy) at policy-load time, in addition
to the canonical development_commit/review_commit handlers registered at import time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from git import InvalidGitRepositoryError
from loguru import logger

from ralph.git.operations import GitOperationError, has_uncommitted_changes
from ralph.mcp.artifacts.commit_message import COMMIT_MESSAGE_ARTIFACT
from ralph.phases import PhaseContext, register_handler
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent

if TYPE_CHECKING:
    from ralph.pipeline.events import Event


def _has_no_diff(ctx: PhaseContext) -> bool:
    """Best-effort no-diff check; returns False when git cannot be inspected.

    Catches both workspace-layer failures (AttributeError/TypeError/ValueError
    from mock workspaces that do not implement ``absolute_path``) and git-layer
    failures (non-repo filesystem paths). In either case the caller falls back
    to the legacy defer-to-runner path so the pipeline makes progress.
    """
    try:
        root = ctx.workspace.absolute_path(".")
        return not has_uncommitted_changes(root)
    except (
        AttributeError,
        TypeError,
        ValueError,
        OSError,
        InvalidGitRepositoryError,
        GitOperationError,
    ):
        return False


def handle_commit_phase(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Generic commit phase handler for any role='commit' phase.

    Stages and commits changes after a successful phase. When the working
    tree has no pending changes, emits ``COMMIT_SKIPPED`` so the pipeline
    advances without billing a progress counter for the no-op pass.
    """
    if not isinstance(effect, InvokeAgentEffect):
        return []

    phase_name = effect.phase

    if _has_no_diff(ctx):
        logger.info("{}: no diff to commit — skipping", phase_name)
        return [PipelineEvent.COMMIT_SKIPPED]

    # Validate the commit_message artifact was submitted by the agent.
    # The runner clears COMMIT_MESSAGE_ARTIFACT before agent invocation, so absence
    # here means the agent completed without submitting the required artifact.
    if not ctx.workspace.exists(COMMIT_MESSAGE_ARTIFACT):
        logger.warning(
            "{} agent completed without producing {}",
            phase_name,
            COMMIT_MESSAGE_ARTIFACT,
        )
        return [
            PhaseFailureEvent(
                phase=phase_name,
                reason=(
                    f"Missing commit_message artifact at {COMMIT_MESSAGE_ARTIFACT}; "
                    "the agent must submit commit_message before declaring completion"
                ),
                recoverable=True,
                retry_in_session=True,
            )
        ]

    logger.info("{}: deferring commit execution to runner", phase_name)
    return []


@register_handler("development_commit")
def handle_development_commit(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Handle the development commit phase."""
    return handle_commit_phase(effect, ctx)


@register_handler("review_commit")
def handle_review_commit(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Handle the review commit phase."""
    return handle_commit_phase(effect, ctx)


def handle_commit(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Compatibility wrapper for commit handling.

    Dispatches to the generic commit phase handler for any commit-role phase.
    """
    if isinstance(effect, (InvokeAgentEffect, PreparePromptEffect)):
        return handle_commit_phase(effect, ctx)
    return []
