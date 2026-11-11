
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
    pre_contents = capture_pre_write_contents(workspace_scope.root, [AGENTS_MD])
    try:
        policy_agents_md.condense_placeholder_block(workspace, assessment=assessment)
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


_finalize_ready_state = finalize_ready_state
_auto_commit_policy_changes = auto_commit_policy_changes
_commit_policy_changes = commit_policy_changes
