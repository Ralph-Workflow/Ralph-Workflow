"""Mandatory planning integration, including dirty-worktree-safe landing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.merge_obstructions import ancestry_state
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate import _integrate_once, auto_integrate_after_commit
from ralph.pipeline.auto_integrate_catchup import resolve_integration_target
from ralph.pipeline.auto_integrate_outcome import record_conflict
from ralph.pipeline.auto_integrate_record import read_record
from ralph.pipeline.auto_integrate_recovery import (
    recover_incomplete_integration,
    recovery_retained_record,
)
from ralph.pipeline.auto_integrate_worktree_state import _worktree_is_clean

if TYPE_CHECKING:
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
    if not config.general.auto_integrate_enabled:
        return None
    if read_record(root) is not None:
        recovered = recover_incomplete_integration(
            scope, config=config, conflict_resolver=conflict_resolver,
            rebase_stop_resolver=rebase_stop_resolver,
        )
        if recovery_retained_record(recovered):
            return recovered
        if recovered is not None and recovered.fast_forwarded:
            return recovered
    target = resolve_integration_target(config, root)
    if target is None or _worktree_is_clean(root):
        return auto_integrate_after_commit(
            config, scope, state, conflict_resolver=conflict_resolver,
            rebase_stop_resolver=rebase_stop_resolver, display=display,
        )
    from ralph.pipeline.auto_integrate_recovery_terminal import post_attempt_verify

    post_attempt_verify(root, expected_head_sha=None, owns_resolution=False)
    if ancestry_state(root, target, "HEAD") is not True:
        staged = run_git(("diff", "--cached", "--quiet"), cwd=root, label="planning:staged-work")
        if staged.returncode != 0:
            return record_conflict(reason="staged uncommitted work blocks integration", target=target)
    outcome, _retry = _integrate_once(
        config, root, target, conflict_resolver, force_endpoint_merge=True,
        rebase_stop_resolver=rebase_stop_resolver, display=display,
    )
    return outcome
