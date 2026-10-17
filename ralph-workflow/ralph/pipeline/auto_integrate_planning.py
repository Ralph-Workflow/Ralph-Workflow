"""Mandatory planning integration, including dirty-worktree-safe landing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.merge_obstructions import ancestry_state
from ralph.git.subprocess_runner import run_git
from ralph.pipeline._planning_obstruction import PlanningObstructionKind
from ralph.pipeline.auto_integrate import _integrate_once, auto_integrate_after_commit
from ralph.pipeline.auto_integrate_catchup import resolve_integration_target
from ralph.pipeline.auto_integrate_outcome import record_conflict
from ralph.pipeline.auto_integrate_recovery import (
    recover_incomplete_integration,
    recovery_retained_record,
)
from ralph.pipeline.auto_integrate_worktree_state import _worktree_is_clean
from ralph.pipeline.integration_resolution import retained_integration_reason

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope


def integrate_before_planning(
    config: UnifiedConfig,
    scope: WorkspaceScope,
    state: RebaseState,
    *,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
) -> RebaseState | None:
    """Land committed work without stashing or committing unfinished edits.

    A dirty tree can publish an already synchronized committed tip. Otherwise
    use Git's non-destructive merge refusal, with a clean index so the merge
    cannot sweep staged agent work into history.
    """
    root = scope.root
    if retained_integration_reason(root) is not None:
        from ralph.pipeline._integration_continuation import config_for_owned_integration

        config = config_for_owned_integration(config)
        recovered = recover_incomplete_integration(
            scope,
            config=config,
            conflict_resolver=conflict_resolver,
            rebase_stop_resolver=rebase_stop_resolver,
        )
        if recovery_retained_record(recovered):
            return recovered
        if recovered is not None and recovered.fast_forwarded:
            return recovered
    if not config.general.auto_integrate_enabled:
        return None
    target = resolve_integration_target(config, root)
    if target is None or _worktree_is_clean(root):
        return auto_integrate_after_commit(
            config,
            scope,
            state,
            conflict_resolver=conflict_resolver,
            rebase_stop_resolver=rebase_stop_resolver,
            display=display,
        )
    from ralph.pipeline.auto_integrate_recovery_terminal import post_attempt_verify

    post_attempt_verify(root, expected_head_sha=None, owns_resolution=False)
    if ancestry_state(root, target, "HEAD") is not True:
        staged_block = _record_staged_block(root, target)
        if staged_block is not None:
            return staged_block
    return _integrate_with_force_merge(
        config,
        root,
        target,
        conflict_resolver,
        rebase_stop_resolver=rebase_stop_resolver,
        display=display,
    )


def _record_staged_block(root: Path, target: str) -> RebaseState | None:
    """Record a typed ``STAGED_BLOCKS_INTEGRATION`` planning obstruction.

    The free-form reason is preserved for operator display but the
    planning producer ALSO sets the typed
    ``last_planning_obstruction`` kind on the resulting
    ``RebaseState`` so the run loop can route on the typed signal
    rather than parsing prose.

    Returns ``None`` when no staged work is blocking integration so
    the caller falls through to the normal integration path. The
    return value is the recorded :class:`RebaseState` when staged
    work IS blocking.
    """
    staged = run_git(("diff", "--cached", "--quiet"), cwd=root, label="planning:staged-work")
    if staged.returncode == 0:
        return None
    recorded = record_conflict(reason="staged uncommitted work blocks integration", target=target)
    return recorded.model_copy(
        update={
            "last_planning_obstruction": PlanningObstructionKind.STAGED_BLOCKS_INTEGRATION.value
        }
    )


def _integrate_with_force_merge(
    config: UnifiedConfig,
    root: Path,
    target: str,
    conflict_resolver: ConflictResolver | None,
    *,
    rebase_stop_resolver: RebaseStopResolver | None,
    display: ParallelDisplay | None,
) -> RebaseState | None:
    """Force a non-destructive endpoint merge for the planning integration."""
    outcome, _retry = _integrate_once(
        config,
        root,
        target,
        conflict_resolver,
        force_endpoint_merge=True,
        rebase_stop_resolver=rebase_stop_resolver,
        display=display,
    )
    return outcome
