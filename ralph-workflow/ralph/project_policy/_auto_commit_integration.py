"""Project-policy auto-commit integration helpers.

Owns the deterministic auto-commit wiring that the readiness preflight
(:mod:`ralph.project_policy.preflight`), the policy opt-out flow
(:mod:`ralph.project_policy.cli_integration`), and the post-pipeline
finalize pass call. Lives in its own module so
:mod:`ralph.project_policy.cli_integration` stays under the
1000-line repository cap and the integration-level commit wiring
(snapshot of pre-write content, opt-out marker, AGENTS.md condense
+ commit) is reviewable in one place.

The git-level commit primitives live in :mod:`ralph.project_policy._auto_commit`;
this module wraps them with the call-site concerns (input gathering,
working-tree pre-write capture, post-condense finalize, ``pre_run_dirty``
subtraction) and threads the result through the orchestrator's
READY/NOT-READY exit branches. Failures are logged and swallowed at
every entry point: a broken git state must never block the run.

wt-012: every entry point routes through the shared isolation
primitive in :mod:`ralph.git.scoped_auto_commit` so a user- or
agent-dirty path is SKIPPED with a warning instead of being swept
into the fixed-message policy commit. The producer-level
:func:`commit_policy_writes` call records the pre-write content hash
BEFORE the deterministic writer runs, so the SKIP decision uses the
file's HEAD state and not the post-write state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.operations import create_commit
from ralph.git.scoped_auto_commit import ScopedCommitStatus, capture_pre_write_contents
from ralph.project_policy import _auto_commit as policy_auto_commit
from ralph.project_policy import agents_md as policy_agents_md
from ralph.project_policy import cache as policy_cache
from ralph.project_policy import markers as policy_markers
from ralph.project_policy import models as policy_models

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.language_detector.models import ProjectStack
    from ralph.workspace.protocol import Workspace
    from ralph.workspace.scope import WorkspaceScope


    #: ``_finalize_ready_state`` accepts a custom policy-commit callable so
    #: tests can substitute a no-op spy without monkey-patching
    #: ``_commit_policy_changes``. Mirrors the ``PolicyCommit`` alias in
    #: :mod:`ralph.project_policy.cli_integration`.
    _PolicyCommitFn = Callable[["WorkspaceScope", frozenset[str] | None], None]


def _write_and_commit_opt_out(workspace: Workspace) -> None:
    """Write the opt-out marker and commit it immediately (wt-012 DA-001/DA-010).

    The opt-out marker write is a deterministic engine-owned write (append
    the byte-exact marker if missing), so the fixed policy chore commit
    must carry it right after the write. Without the commit the preflight
    exits SKIPPED -- the post-pipeline policy commit never runs -- and the
    marker lingers as uncommitted background dirt that leaks into a later
    agent commit. An AGENTS.md already dirty at HEAD is SKIPPED by the
    shared isolation primitive and stays in the user/agent flow; a commit
    failure is reported and never blocks the run.
    """
    from pathlib import Path  # noqa: PLC0415

    root: object = getattr(workspace, "root", None)
    pre_contents: dict[str, str | None] = (
        capture_pre_write_contents(root, [policy_markers.AGENTS_MD])
        if isinstance(root, Path)
        else {policy_markers.AGENTS_MD: None}
    )
    written = policy_agents_md.write_opt_out(workspace)
    if not written or not isinstance(root, Path):
        return
    try:
        result = policy_auto_commit.commit_policy_writes(
            root,
            written_paths=written,
            pre_contents=pre_contents,
            create_commit_fn=create_commit,
        )
    except Exception as exc:  # defensive: the commit must never block the run
        logger.warning("policy opt-out auto-commit raised (non-fatal): {}", exc)
        return
    if result.status is ScopedCommitStatus.CREATED:
        logger.info(
            "policy opt-out: committed opt-out marker ({})", (result.sha or "")[:12]
        )
    elif result.status is ScopedCommitStatus.FAILED:
        logger.warning("policy opt-out: marker commit failed (non-fatal): {}", result.error)
    elif result.status is ScopedCommitStatus.SKIPPED:
        logger.warning(
            "policy opt-out: marker commit skipped dirty path(s): {}",
            ", ".join(result.skipped_paths),
        )


def _commit_policy_changes(
    workspace_scope: WorkspaceScope,
    pre_run_dirty: frozenset[str] | None,
) -> None:
    _auto_commit_policy_changes(workspace_scope, pre_run_dirty)


def _finalize_ready_state(
    workspace: Workspace,
    workspace_scope: WorkspaceScope,
    stack: ProjectStack,
    pre_run_dirty: frozenset[str] | None = None,
    *,
    commit_policy_updates: _PolicyCommitFn = _commit_policy_changes,
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
    # Record AGENTS.md's pre-write content hash BEFORE condense rewrites
    # it. The producer-level commit uses the hash to detect that the
    # file was already dirty at HEAD and SKIP the path -- agent
    # edits stay in the agent flow.
    from ralph.project_policy.markers import AGENTS_MD  # noqa: PLC0415

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
            logger.warning(
                "project-policy auto-commit failed (non-fatal): {}", result.error
            )
    except Exception as exc:
        logger.warning("project-policy auto-commit failed (non-fatal): {}", exc)
    try:
        policy_cache.write_cache(workspace, stack, policy_models.ReadinessStatus.READY)
    except Exception as exc:
        logger.debug("project-policy READY cache write failed (non-fatal): {}", exc)


def _auto_commit_policy_changes(
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


__all__ = [
    "_auto_commit_policy_changes",
    "_commit_policy_changes",
    "_finalize_ready_state",
    "_write_and_commit_opt_out",
]
