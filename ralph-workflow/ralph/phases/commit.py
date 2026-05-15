"""Commit phase handler.

Commit-role phases handle git operations after successful development or review
phases. They stage and commit changes with an appropriate message.

If the working tree has no uncommitted changes when a commit phase is entered,
the handler emits ``COMMIT_SKIPPED`` so the reducer can advance routing without
incrementing iteration/reviewer_pass counters for a no-op pass.

The generic handle_commit_phase() function works for any phase with role='commit'.
It is registered exclusively via register_role_handlers(policy) at policy-load time
for all commit-role phases declared in the active pipeline policy.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from git import InvalidGitRepositoryError
from loguru import logger

from ralph.git.operations import GitOperationError, has_uncommitted_changes
from ralph.mcp.artifacts.commit_message import COMMIT_MESSAGE_ARTIFACT, read_commit_message_artifact
from ralph.phases.artifacts import artifact_validation_failure_event
from ralph.pipeline.effects import Effect, InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent

if TYPE_CHECKING:
    from ralph.phases import PhaseContext
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


def _read_commit_message(ctx: PhaseContext) -> str | None:
    """Read the commit message artifact content from the workspace.

    Returns None when the artifact is absent, the workspace does not support
    absolute paths (e.g., mock), or the artifact is unreadable.
    """
    try:
        root = ctx.workspace.absolute_path(".")
        return read_commit_message_artifact(Path(root))
    except Exception:
        return None


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
            artifact_validation_failure_event(
                phase=phase_name,
                reason=(
                    f"Missing commit_message artifact at {COMMIT_MESSAGE_ARTIFACT}; "
                    "the agent must submit commit_message before declaring completion"
                ),
            )
        ]

    # Artifact exists — check if the agent submitted a skip response.
    # Without this guard, a skip artifact would be passed to the runner
    # and committed verbatim as a "SKIP: ..." git commit subject.
    message = _read_commit_message(ctx)
    if message is not None and message.strip().lower().startswith("skip:"):
        logger.info("{}: commit agent requested skip — skipping", phase_name)
        return [PipelineEvent.COMMIT_SKIPPED]

    logger.info("{}: deferring commit execution to runner", phase_name)
    return []
