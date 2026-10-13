"""Post-READY finalization and policy auto-commit helpers.

Split from :mod:`ralph.project_policy.cli_integration` to keep that module
well under the 1000-line repository cap while isolating the post-pipeline
git auto-commit and READY cache finalization logic.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.operations import create_commit
from ralph.git.scoped_auto_commit import capture_pre_write_contents
from ralph.project_policy import _auto_commit as policy_auto_commit
from ralph.project_policy import agents_md as policy_agents_md
from ralph.project_policy import cache as policy_cache
from ralph.project_policy import models as policy_models
from ralph.project_policy.markers import AGENTS_MD

if TYPE_CHECKING:
    from ralph.language_detector.models import ProjectStack
    from ralph.workspace.protocol import Workspace
    from ralph.workspace.scope import WorkspaceScope

PolicyCommit = Callable[["WorkspaceScope", frozenset[str] | None], None]


def auto_commit_policy_changes(
    workspace_scope: WorkspaceScope,
    pre_run_dirty: frozenset[str] | None = None,
) -> None:
    """Best-effort deterministic auto-commit of the policy surfaces (post-pipeline).

    wt-012: this is the safety-net pass that runs after the preflight's
    producer-level commit. The preflight already committed the surfaces
    it wrote; this pass picks up any remaining dirty policy surfaces
    (e.g. ones that became dirty between the preflight and the post-
    pipeline finalize) and commits them with the same exclusion
    discipline as before. Agent-authored paths (gate scripts outside
    the policy directories) are NOT swept in here; they stay in the
    agent commit flow.

    ``pre_run_dirty`` is subtracted from the in-scope set so a user
    mid-edit on ``AGENTS.md`` or a policy file is never swept in.
    Failures are logged and swallowed -- a broken git state must not
    block the run.
    """
    try:
        sha = policy_auto_commit.commit_policy_updates(
            workspace_scope.root,
            create_commit,
            pre_run_dirty=pre_run_dirty,
        )
        if sha is not None:
            logger.debug("project-policy auto-commit created: {}", sha)
    except Exception as exc:
        logger.debug("project-policy auto-commit failed (non-fatal): {}", exc)


def commit_policy_changes(
    workspace_scope: WorkspaceScope,
    pre_run_dirty: frozenset[str] | None,
) -> None:
    """Commit dirty policy surfaces to git after policy finalization.

    Convenience wrapper around :func:`auto_commit_policy_changes` conforming
    to the :data:`PolicyCommit` callable signature.

    Args:
        workspace_scope: Scope of the target workspace for git operations.
        pre_run_dirty: Set of paths dirty before the run, excluded from auto-commit.
    """
    auto_commit_policy_changes(workspace_scope, pre_run_dirty)


def finalize_ready_state(
    workspace: Workspace,
    workspace_scope: WorkspaceScope,
    stack: ProjectStack,
    pre_run_dirty: frozenset[str] | None = None,
    *,
    commit_policy_updates: PolicyCommit = commit_policy_changes,
    assessment: policy_models.ReadinessAssessment | None = None,
    command_outcomes: tuple[policy_models.CommandOutcome, ...] = (),
) -> None:
    """Post-READY housekeeping: condense the temporary AGENTS.md placeholder
    block to its concise form, commit the policy surfaces, then write the
    READY cache against the tree the run actually leaves behind.

    The cache write is the FINAL step so the cached signature is taken
    over the tree the run leaves (condense + auto-commit both write
    first). Writing the cache earlier -- as the old
    ``run_policy_readiness_preflight`` and ``pipeline_driver._finish``
    both did -- produces a stale signature that flunks the next
    preflight into a full re-validation for work that is already
    done. ``stack`` is required here because the cache signature is
    the project-stack's view of the evidence inventory.

    wt-012: the post-pipeline commit routes through the producer-level
    :func:`commit_policy_writes` helper with the pre-write content
    hash of AGENTS.md recorded BEFORE ``condense_placeholder_block``
    runs. If the file was already dirty at HEAD (an agent edited it
    during the run), the path is SKIPPED with a warning and stays in
    the agent flow; it can never enter a fixed-message policy commit.
    """
    pre_contents = capture_pre_write_contents(workspace_scope.root, [AGENTS_MD])
    try:
        policy_agents_md.condense_placeholder_block(workspace)
    except Exception as exc:
        logger.debug("AGENTS.md placeholder condense failed (non-fatal): {}", exc)
    try:
        result = policy_auto_commit.commit_policy_writes(
            workspace_scope.root,
            written_paths=[AGENTS_MD],
            pre_contents=pre_contents,
            create_commit_fn=create_commit,
        )
        if result.status.value == "created" and result.sha:
            logger.debug("project-policy auto-commit created: {}", result.sha)
        elif result.status.value == "skipped":
            logger.debug(
                "project-policy auto-commit skipped: AGENTS.md was already dirty at HEAD "
                "(agent edit?)"
            )
        elif result.status.value == "failed":
            logger.debug("project-policy auto-commit failed (non-fatal): {}", result.error)
    except Exception as exc:
        logger.debug("project-policy auto-commit failed (non-fatal): {}", exc)
    try:
        policy_cache.write_cache(
            workspace,
            stack,
            policy_models.ReadinessStatus.READY,
            assessment=assessment,
            command_outcomes=command_outcomes,
        )
    except Exception as exc:
        logger.debug("project-policy READY cache write failed (non-fatal): {}", exc)


# Aliases for backward compatibility with private references
_finalize_ready_state = finalize_ready_state
_auto_commit_policy_changes = auto_commit_policy_changes
_commit_policy_changes = commit_policy_changes
