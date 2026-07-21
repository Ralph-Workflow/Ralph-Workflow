"""Auto-integration: rebase the feature branch onto the mainline after each commit.

After every commit phase that actually creates a commit, the Ralph
pipeline runs :func:`auto_integrate_after_commit` to keep the
feature branch and the local mainline ref in lockstep:

1. **Rebase first.** Rebase the feature branch onto the resolved
   target tip.
2. **Merge on conflict.** If the rebase conflicts, abort it cleanly
   and attempt one merge of the target branch into the feature
   branch. A single endpoint three-way merge often succeeds where
   commit-by-commit replay conflicts, and it still makes the
   target an ancestor of the feature branch, preserving
   fast-forwardability.
3. **Give up gracefully.** If the merge also conflicts, abort it,
   leave the feature branch bit-for-bit untouched, record the
   outcome, and let the run continue. The step retries after the
   next commit phase.

Once the feature branch fully contains the target, fast-forward
the local mainline ref to the feature tip — never force-moved, only
forwarded via the atomic compare-and-swap path or the worktree's
own ``git merge --ff-only``.

Crash safety is provided by :func:`recover_incomplete_integration`
which runs at startup and reconciles any durable, atomically-written
``IntegrationRecord`` left by an interrupted run.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import (
    MergeResult,
    branch_exists,
    branch_sha,
    resolve_origin_head_branch,
)
from ralph.git.operations import GitOperationError, get_head_sha
from ralph.git.rebase import (
    RebasePreconditionError,
    check_rebase_preconditions,
)
from ralph.git.rebase.rebase import (
    RebaseConflicts,
    RebaseFailed,
    RebaseNoOp,
    RebaseSuccess,
    abort_rebase,
    rebase_in_progress,
    rebase_onto,
)
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate_conflict_budget import (
    apply_conflict_budget,
    resolver_allowed,
)
from ralph.pipeline.auto_integrate_ff import (
    fast_forward_target,
    is_retryable_fast_forward_failure,
)
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
)
from ralph.pipeline.auto_integrate_record import (
    clear_record as _clear_record,
)
from ralph.pipeline.auto_integrate_record import (
    write_record as _write_record,
)
from ralph.pipeline.auto_integrate_recovery import (
    recover_incomplete_integration,
)
from ralph.pipeline.auto_integrate_refresh import (
    refresh_target as _refresh_target,
)
from ralph.pipeline.auto_integrate_resolve import (
    RESOLUTION_FAILED,
    endpoint_merge_with_resolution,
)
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.workspace.scope import WorkspaceScope


#: Outcome verbs recorded on ``RebaseState.last_action`` so the runner
#: can format the user-facing log line (rebased / merged /
#: skipped / conflict / recovered). The landing result is recorded
#: on the separate ``fast_forwarded`` boolean, not as an action verb.
_ACTION_SKIPPED = "skipped"
_ACTION_REBASED = "rebased"
_ACTION_MERGED = "merged"
_ACTION_CONFLICT = "conflict"
_ACTION_RECOVERED = "recovered"


#: Target branch resolution order when ``auto_integrate_target`` is unset.
#: Mirrors the prompt's ``origin/HEAD`` -> ``main`` -> ``master`` cascade.
_AUTO_DETECT_TARGET_CANDIDATES: tuple[str, ...] = ("main", "master")


def resolve_integration_target(
    config: UnifiedConfig,
    repo_root: Path | str,
) -> str | None:
    """Resolve the integration target branch name from config and the repo.

    Precedence:

    1. ``config.general.auto_integrate_target`` when set: used verbatim
       when the branch exists locally OR can be materialized from
       ``refs/remotes/origin/<branch>`` (AC-13).
    2. Else: ``origin/HEAD`` remote default branch when it resolves.
    3. Else: the first existing branch in
       ``('main', 'master')``.
    4. Else: ``None`` — the step skips with a recorded reason and
       mutates nothing.
    """
    repo_root_path = Path(repo_root)
    configured: str | None = None
    configured_attr: object = getattr(config.general, "auto_integrate_target", None)
    if isinstance(configured_attr, str) and configured_attr:
        configured = configured_attr
    if isinstance(configured, str) and configured:
        # Same materialization the auto-detect path below uses: a
        # clone-layout agent that pins its target explicitly must not be
        # worse off than one that lets Ralph detect it.
        if _ensure_local_origin_branch(repo_root_path, configured):
            return configured
        return None

    origin_default = resolve_origin_head_branch(repo_root_path)
    if origin_default and _ensure_local_origin_branch(repo_root_path, origin_default):
        return origin_default

    for candidate in _AUTO_DETECT_TARGET_CANDIDATES:
        if _ensure_local_origin_branch(repo_root_path, candidate):
            return candidate

    return None


def _ensure_local_origin_branch(repo_root: Path, branch: str) -> bool:
    """Return whether ``branch`` is local, materializing ``origin/branch`` if needed.

    A clone-style agent worktree commonly has only ``origin/main``.  The
    local mainline ref is required for the later CAS/fast-forward contract,
    so create it from that remote-tracking ref before integration.  Git
    refuses an already-created branch; re-checking makes that race harmless.
    """
    if branch_exists(repo_root, branch):
        return True
    remote_ref = f"refs/remotes/origin/{branch}"
    if run_git(
        ("show-ref", "--verify", "--quiet", remote_ref),
        cwd=repo_root,
        label="git-origin-target-exists",
    ).returncode != 0:
        return False
    run_git(
        ("branch", "--", branch, remote_ref),
        cwd=repo_root,
        label="git-create-local-origin-target",
    )
    return branch_exists(repo_root, branch)


# The record path / write_record / read_record / clear_record helpers
# were extracted to :mod:`ralph.pipeline.auto_integrate_record` to keep
# this module under the repo-structure ``_MAX_FILE_LINES`` cap. The
# module-level ``from ... import ... as _xxx_record`` aliases above
# expose them under the original private names so the call sites in
# this module read unchanged.


def _record_skip(
    *,
    reason: str,
    target: str | None,
    fast_forwarded: bool = False,
) -> RebaseState:
    """Build a ``RebaseState`` recording a skip outcome."""
    return RebaseState(
        last_action=_ACTION_SKIPPED,
        last_reason=reason,
        last_target=target,
        fast_forwarded=fast_forwarded,
    )


def _record_conflict(
    *,
    reason: str,
    target: str | None,
) -> RebaseState:
    """Build a ``RebaseState`` recording a conflict outcome (AC-07)."""
    return RebaseState(
        last_action=_ACTION_CONFLICT,
        last_reason=reason,
        last_target=target,
        fast_forwarded=False,
    )


def _record_rebase_outcome(
    *,
    rebase_outcome: RebaseSuccess | RebaseConflicts | RebaseNoOp | RebaseFailed,
    merge_attempted: bool,
    merge_outcome: MergeResult | None,
    target: str,
) -> RebaseState:
    """Build a ``RebaseState`` from the rebase + optional merge result.

    Success path -> ``last_action='rebased'`` or ``'merged'`` and
    ``fast_forwarded`` is set by the caller after the fast-forward
    phase; the function leaves ``fast_forwarded=False`` here so the
    caller can update it once the CAS / worktree-ff step completes.

    The mapping is centralized in :func:`_classify_rebase_outcome`
    so this function is a thin constructor over the (action, reason)
    pair it returns.
    """
    action, reason = _classify_rebase_outcome(
        rebase_outcome=rebase_outcome,
        merge_attempted=merge_attempted,
        merge_outcome=merge_outcome,
    )
    return RebaseState(
        last_action=action,
        last_reason=reason,
        last_target=target,
        fast_forwarded=False,
    )


def _classify_rebase_conflict_outcome(
    *,
    merge_attempted: bool,
    merge_outcome: MergeResult | None,
) -> tuple[str, str | None] | None:
    """Sub-classifier for the ``RebaseConflicts`` branch.

    Returns ``None`` when this sub-classifier cannot decide (e.g.
    the merge was not attempted at all and we still want the
    generic "rebase conflicts" headline to be raised by the caller).
    Otherwise returns the ``(action, reason)`` pair to use as the
    :func:`_record_rebase_outcome` headline.
    """
    if not merge_attempted or merge_outcome is None:
        return None
    # The rebase conflicted; the endpoint merge that followed
    # is recorded in ``merge_outcome.outcome``. A conflicting
    # merge is the AC-07 "both conflicted" case (headline:
    # conflict). A clean success/noop merge is the AC-06
    # "rebase conflicted but endpoint merge succeeded" case
    # (headline: merged) -- the rebase-apply/rebase-merge state
    # was already aborted by ``_resolve_rebase_conflict`` so the
    # resulting branch state is the merged tree.
    if merge_outcome.outcome == RESOLUTION_FAILED:
        return _ACTION_CONFLICT, "conflict resolution failed; merge aborted"
    if merge_outcome.outcome == "conflict":
        return _ACTION_CONFLICT, "rebase and endpoint merge both conflicted"
    if merge_outcome.outcome in {"success", "noop"}:
        return _ACTION_MERGED, None
    return None


def _classify_rebase_outcome(
    *,
    rebase_outcome: RebaseSuccess | RebaseConflicts | RebaseNoOp | RebaseFailed,
    merge_attempted: bool,
    merge_outcome: MergeResult | None,
) -> tuple[str, str | None]:
    """Map a rebase + optional merge result to a ``(action, reason)`` pair.

    Split out from :func:`_record_rebase_outcome` to keep the
    headline builder within the ruff PLR0911 return-statement cap
    (the builder is a single-return constructor; this classifier
    owns the branch table).
    """
    if isinstance(rebase_outcome, RebaseConflicts):
        sub = _classify_rebase_conflict_outcome(
            merge_attempted=merge_attempted,
            merge_outcome=merge_outcome,
        )
        if sub is not None:
            return sub
        return _ACTION_CONFLICT, "rebase conflicts"
    if isinstance(rebase_outcome, RebaseNoOp):
        # No-op is recorded as rebased (no work done, but the
        # branch is already aligned with target).
        return _ACTION_REBASED, rebase_outcome.reason
    if isinstance(rebase_outcome, RebaseFailed):
        return _classify_rebase_failed_outcome(
            rebase_outcome=rebase_outcome,
            merge_attempted=merge_attempted,
            merge_outcome=merge_outcome,
        )
    # RebaseSuccess or after a clean endpoint merge.
    if merge_attempted and merge_outcome is not None:
        return _ACTION_MERGED, None
    return _ACTION_REBASED, None


def _classify_rebase_failed_outcome(
    *,
    rebase_outcome: RebaseFailed,
    merge_attempted: bool,
    merge_outcome: MergeResult | None,
) -> tuple[str, str | None]:
    """Sub-classifier for the ``RebaseFailed`` branch.

    The endpoint-merge fallback runs for failed rebases too; the same
    sub-table applies, with the failed-rebase wording substituted into
    the merge-conflicted headline and a skip when no merge ran.
    """
    sub = _classify_rebase_conflict_outcome(
        merge_attempted=merge_attempted,
        merge_outcome=merge_outcome,
    )
    if sub is None:
        return _ACTION_SKIPPED, f"rebase failed: {rebase_outcome.kind}"
    if sub == (_ACTION_CONFLICT, "rebase and endpoint merge both conflicted"):
        return _ACTION_CONFLICT, "rebase failed and endpoint merge conflicted"
    return sub


def _fast_forward_target(
    repo_root: Path,
    target: str,
    feature_sha: str,
) -> tuple[bool, str]:
    """Backwards-compat shim for the extracted fast-forward path.

    The implementation now lives in
    :mod:`ralph.pipeline.auto_integrate_ff`; this wrapper remains
    so :func:`_continue_fast_forward_from_record` (and any future
    in-module caller) can keep referencing the local symbol.
    """
    return fast_forward_target(repo_root, target, feature_sha)


def auto_integrate_after_commit(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    state: RebaseState,
    *,
    conflict_resolver: ConflictResolver | None = None,
) -> RebaseState | None:
    """Run the auto-integration step after a successful commit.

    Args:
        config: Unified run configuration (enable flag + target).
        workspace_scope: Scope whose root is the feature repository.
        state: Prior rebase state (unused today; kept for the seam).
        conflict_resolver: Optional callable handed a conflicted
            in-progress endpoint merge to resolve and stage (in
            production a focused dev-agent invocation). The merge
            commit itself is always created deterministically here,
            never by the resolver.

    Returns:
        ``None`` when the feature is disabled (AC-01: byte-identical
        no-op for runs with ``auto_integrate_enabled = false``).
        Otherwise a :class:`RebaseState` recording either an
        outcome (``rebased``/``merged``/``fast_forwarded``), a skip
        (with ``last_reason``), or a conflict (``last_action='conflict'``).

    Never raises: the entire body is wrapped in a broad try/except
    that converts an unexpected failure into a skip record so a
    broken integration step can never abort the surrounding run.
    """
    try:
        return _auto_integrate_after_commit_inner(
            config, workspace_scope, state, conflict_resolver
        )
    except Exception as exc:
        logger.warning("auto_integrate_after_commit: unexpected failure: {}", exc)
        with contextlib.suppress(Exception):
            _clear_record(Path(workspace_scope.root))
        return _record_skip(reason=f"unexpected failure: {exc}", target=None)


def auto_integrate_on_phase_transition(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    state: RebaseState,
    *,
    conflict_resolver: ConflictResolver | None = None,
) -> RebaseState | None:
    """Run the integration step at a phase boundary when it can help.

    The commit seam already integrates after every real commit; this
    hook keeps the feature branch in lockstep with the target between
    commits too (e.g. the target advanced while an analysis phase
    ran). It is deliberately quiet: phase boundaries are frequent, so
    the hook returns ``None`` without recording anything when

    * the worktree is dirty (mid-phase uncommitted work — integrating
      would be unsafe and the next commit seam will catch up), or
    * the resolved target already sits at the feature tip (nothing to
      rebase, nothing to land).

    Otherwise it runs the full integration (rebase → endpoint merge →
    optional agent conflict resolution → fast-forward) and returns
    the recorded outcome. Never raises.
    """
    try:
        root = Path(workspace_scope.root)
        # Cheap stat guard BEFORE any subprocess: phase boundaries are
        # frequent, and a workspace that is not a git checkout (or a
        # disabled feature) must cost nothing here.
        enabled: object = getattr(config.general, "auto_integrate_enabled", True)
        if not enabled or not (root / ".git").exists():
            return None
        target = resolve_integration_target(config, root)
        if target is None:
            return None
        if not _worktree_is_clean(root):
            logger.info(
                "auto_integrate: phase-transition integration deferred; "
                "worktree dirty (target '{}')",
                target,
            )
            return None
        # A stale remote pointer must not let this cheap hook conclude
        # 'nothing to do'. Every free early return above still costs
        # nothing.
        _refresh_target(config, root, target)
        target_sha = branch_sha(root, target)
        if target_sha is not None and target_sha == get_head_sha(root):
            # Fully integrated and landed: the frequent-boundary case.
            return None
    except Exception as exc:
        logger.warning(
            "auto_integrate: phase-transition pre-check failed: {}", exc
        )
        return None
    return auto_integrate_after_commit(
        config, workspace_scope, state, conflict_resolver=conflict_resolver
    )


def _worktree_is_clean(root: Path) -> bool:
    """True when ``git status --porcelain`` reports no changes.

    Deliberately asymmetric with
    :func:`ralph.git.rebase.rebase_preconditions._ensure_clean_worktree`,
    which tolerates untracked files: this guard runs at every phase
    boundary, mid-cycle, while a development agent may have just
    created files it has not committed yet. Integrating here is
    opportunistic, never required -- the commit seam catches up a
    moment later under the relaxed preconditions -- so untracked work
    in flight is reason enough to defer.

    Fails closed (False) on any git failure so the phase-transition
    hook never integrates on top of a worktree it cannot prove clean.
    """
    result = run_git(
        ("status", "--porcelain"),
        cwd=root,
        label="git-transition-status",
    )
    if result.returncode != 0:
        return False
    return not result.stdout.strip()


#: Maximum end-to-end integration attempts per commit. Attempt N+1 runs
#: only when attempt N completed a rebase/merge but the fast-forward
#: did not land (e.g. the target advanced concurrently, AC-08); the
#: retry re-integrates onto the moved tip instead of waiting for the
#: next commit phase.
_MAX_INTEGRATION_ATTEMPTS = 3


def _auto_integrate_after_commit_inner(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    state: RebaseState,
    conflict_resolver: ConflictResolver | None,
) -> RebaseState | None:
    """Internal worker for :func:`auto_integrate_after_commit`.

    The body is split into three narrow phases so each phase keeps a
    sensible branch / statement count without losing the
    skip-condition table from the product brief:

    1. :func:`_auto_integrate_resolve_context` -- handle the
       ``enabled`` / env-lookup / on-target / no-commits-beyond /
       preconditions skip conditions.
    2. :func:`_integrate_once` -- write the crash record, run the
       rebase engine, fall back to the endpoint merge on conflict or
       failure (optionally agent-resolved), then fast-forward.
    3. Bounded retry -- when the fast-forward did not land because
       the target moved concurrently, refresh the target from origin
       and re-integrate onto the moved tip up to
       :data:`_MAX_INTEGRATION_ATTEMPTS` times.

    The incoming ``state`` is the seam its docstring promised: it
    carries the consecutive-unresolved-conflict count that
    :mod:`ralph.pipeline.auto_integrate_conflict_budget` uses to stop
    re-invoking the dev agent on a conflict it has already failed to
    resolve.
    """
    ctx = _auto_integrate_resolve_context(config, workspace_scope)
    early_skip, usable_ctx = _check_early_skips(ctx)
    if early_skip is not None:
        return early_skip
    if usable_ctx is None:
        # Disabled (AC-01) or env lookup failed: caller already
        # recorded the skip when applicable.
        return None
    root, _current_branch, target = usable_ctx

    # Budget check BEFORE any git mutation: an exhausted budget still
    # runs the rebase and the endpoint merge (and still aborts them
    # cleanly), it merely stops paying for another agent invocation.
    allowed = resolver_allowed(state, target)
    effective_resolver = conflict_resolver if allowed else None
    resolver_suppressed = conflict_resolver is not None and not allowed
    if resolver_suppressed:
        logger.warning(
            "auto_integrate: conflict resolution budget exhausted for '{}'; "
            "not invoking the resolver again until an integration lands",
            target,
        )

    record: RebaseState | None = None
    for attempt in range(_MAX_INTEGRATION_ATTEMPTS):
        if attempt:
            # A retry only happens because the target moved under us,
            # so re-read it from origin before re-integrating (AC-03).
            # Attempt 0 is already refreshed by
            # _auto_integrate_resolve_context, so this never doubles
            # the fetch on the common single-attempt path.
            _refresh_target(config, root, target)
        record, retry_ff = _integrate_once(config, root, target, effective_resolver)
        if not retry_ff:
            break
        logger.info(
            "auto_integrate: fast-forward did not land on attempt {}; "
            "re-integrating onto the moved target",
            attempt + 1,
        )
    if record is None:
        return None
    return apply_conflict_budget(
        record,
        prior=state,
        target=target,
        resolver_suppressed=resolver_suppressed,
    )


def _integrate_once(
    config: UnifiedConfig,
    root: Path,
    target: str,
    conflict_resolver: ConflictResolver | None,
) -> tuple[RebaseState | None, bool]:
    """Run one full integrate-and-fast-forward pass.

    Returns ``(record, retry_ff)``. ``retry_ff`` is True ONLY when the
    rebase/merge phase completed but the fast-forward did not land —
    the one case where an immediate re-integration onto the moved
    target can still succeed. Every skip / conflict short-circuit
    returns ``retry_ff=False``.

    ``config`` is threaded in for the pre-landing target refresh: the
    rebase, the endpoint merge and any dev-agent conflict resolution
    (bounded at 900 s by default) all run between context resolution
    and the fast-forward, so the pointer read at context-resolution
    time can be minutes old by the time the landing needs it.
    """
    pre_feature_sha = get_head_sha(root)
    pre_target_sha = branch_sha(root, target)
    # Write the durable crash record BEFORE any git mutation so the
    # recovery preamble can always tell that we own an in-flight
    # integration (AC-11).
    _write_record(
        root,
        IntegrationRecord(
            phase="integrating",
            target=target,
            pre_feature_sha=pre_feature_sha,
            pre_target_sha=pre_target_sha,
        ),
    )

    rebase_result = _run_rebase_or_merge(root, target, conflict_resolver)
    if rebase_result.short_circuit is not None:
        # Resolved failures clear the record; an abort that leaves a rebase
        # in progress retains it for startup recovery.
        return rebase_result.short_circuit, False

    # Success path: the feature branch contains the target.
    feature_sha = _read_post_integration_head_sha(root, target)
    if feature_sha is None:
        return _record_skip(
            reason="post-integration HEAD read failed", target=target
        ), False

    # Flip the durable record to 'integrated' BEFORE any ref move so
    # a crash between this point and the fast-forward will be
    # recoverable as a continue-fast-forward rather than a restore
    # (AC-11).
    _write_record(
        root,
        IntegrationRecord(
            phase="integrated",
            target=target,
            pre_feature_sha=pre_feature_sha,
            pre_target_sha=pre_target_sha,
            integrated_feature_sha=feature_sha,
        ),
    )

    # Re-read the mainline pointer from origin IMMEDIATELY before the
    # fast-forward observes it. Several agents land on the same
    # mainline continuously, so binding the ancestry decision inside
    # fast_forward_target to a pointer read seconds ago (rather than
    # before the rebase/merge/resolution sequence) is what keeps the
    # landing correct under concurrency.
    refresh_outcome = _refresh_target(config, root, target)
    ok, skip_reason = _fast_forward_target(root, target, feature_sha)
    _clear_record(root)

    record = _record_rebase_outcome(
        rebase_outcome=rebase_result.rebase_outcome,
        merge_attempted=rebase_result.merge_attempted,
        merge_outcome=rebase_result.merge_outcome,
        target=target,
    ).model_copy(update={"last_refresh": refresh_outcome})
    if not ok:
        # Fast-forward skipped: reason is appended but we keep
        # the rebase/merged action as the headline so the log line
        # reflects what actually happened to the feature. Only an
        # explicit concurrent target move merits a bounded retry.
        record = record.model_copy(
            update={
                "last_reason": skip_reason,
                "fast_forwarded": False,
            }
        )
        return record, is_retryable_fast_forward_failure(skip_reason)
    # Successful fast-forward: the ``fast_forwarded`` boolean is
    # the headline signal, so any residual reason from the
    # rebase/merge phase (including benign rebase NoOp reasons
    # like ``"Branch is already up-to-date with upstream"``)
    # is scrubbed here. A clean-success state carries no reason.
    record = record.model_copy(
        update={"fast_forwarded": True, "last_reason": None}
    )
    return record, False


def _check_early_skips(
    ctx: tuple[Path, str | None, str | None] | None,
) -> tuple[RebaseState | None, tuple[Path, str, str] | None]:
    """Apply the AC-01/AC-02/AC-13 skip table to the resolved context.

    Returns:
        ``(skip_record, usable_ctx)`` where exactly one of the two
        slots is non-``None``:

        * When ``ctx is None`` (disabled / env lookup failed),
          ``skip_record is None`` and ``usable_ctx is None`` -- the
          caller returns ``None`` directly.
        * When a recorded skip is triggered (AC-02/AC-13), the
          ``RebaseState`` is returned for the caller to return
          directly, and ``usable_ctx is None``.
        * Otherwise, ``skip_record is None`` and ``usable_ctx`` is
          the narrowed context tuple with ``str`` (non-Optional)
          slots the caller can unpack without type ignores.

    Extracted from :func:`_auto_integrate_after_commit_inner` so the
    orchestrator keeps a sensible return-statement count.
    """
    if ctx is None:
        # Disabled (AC-01) or env lookup failed: caller already
        # recorded the skip when applicable.
        return None, None
    root, current_branch, target = ctx
    if current_branch is None:
        # Detached HEAD: AC-02 requires this to be a RECORDED skip,
        # not a silent no-op. The crash record is never written in
        # this branch -- a detached HEAD is not an in-progress
        # integration.
        return _record_skip(reason="detached HEAD", target=target), None
    if target is None:  # AC-13: no target resolved -> recorded skip
        return _record_skip(
            reason="no integration target branch resolved", target=None
        ), None
    skip = _auto_integrate_check_skip_conditions(root, current_branch, target)
    if skip is not None:
        return skip, None
    # No skip: usable_ctx is the narrowed tuple with non-Optional
    # branch + target slots the orchestrator can unpack.
    return None, (root, current_branch, target)


def _read_post_integration_head_sha(
    root: Path, target: str
) -> str | None:
    """Read the feature branch's HEAD after a successful rebase/merge.

    Returns the SHA on success; clears the crash record and returns
    ``None`` on failure so the orchestrator can fall through to its
    final state construction without crashing.

    Extracted from :func:`_auto_integrate_after_commit_inner` so the
    orchestrator keeps a sensible return-statement count.
    """
    try:
        return get_head_sha(root)
    except Exception as exc:
        _clear_record(root)
        logger.warning("auto_integrate: get_head_sha failed post-merge: {}", exc)
        return None


def _current_branch_or_detached_marker(root: Path) -> str | None:
    """Return the current branch name or ``None`` if HEAD is detached.

    Detaches the typed-exception guard from :func:`get_current_branch`'s
    broad fallback so the auto-integrate skip table can record a
    DETACHED-HEAD outcome (AC-02/AC-13 skip condition: "no branch to
    integrate"). GitPython raises ``TypeError`` from
    ``repo.active_branch.name`` when HEAD points at a detached SHA.
    Any other exception -- not a git repo, transport error, etc. --
    propagates so the caller can surface the actual failure.

    Returns:
        The current branch name, or ``None`` when HEAD is detached.
    """
    from git import Repo
    from git.exc import GitCommandError

    repo: Repo | None = None
    try:
        repo = Repo(root)
        return repo.active_branch.name
    except (TypeError, ValueError, GitCommandError, AttributeError):
        # TypeError is GitPython's "DetachedHead has no .name"
        # AttributeError is the same in some GitPython versions.
        # ValueError/GitCommandError cover "no HEAD" / "ambiguous HEAD"
        # edge cases that are also "not on a branch".
        return None
    finally:
        if repo is not None:
            close_method: object = getattr(repo, "close", None)
            if callable(close_method):
                close_method()


def _auto_integrate_resolve_context(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
) -> tuple[Path, str | None, str | None] | None:
    """Resolve the (root, current_branch, target) context or short-circuit.

    Returns ``None`` ONLY when the integration step is a no-op
    (AC-01 disabled path -- byte-identical to the pre-feature run).
    When the environment lookup itself fails (not a git repo,
    transport error, etc.), the exception is allowed to propagate
    so :func:`auto_integrate_after_commit`'s outer try/except can
    record a skip ``RebaseState`` with the failure reason. A
    context-resolution failure must NEVER be silently swallowed to
    ``None`` -- that would make the run continue without any state
    record explaining WHY no integration ran.

    The current-branch slot is ``None`` when HEAD is detached
    (GitPython raises ``TypeError`` from ``repo.active_branch.name``
    in that case). The caller surfaces that as a recorded skip
    (``reason='detached HEAD'``) so the operator can see WHY no
    integration ran -- previously a detached HEAD silently fell into
    the broad ``except Exception`` branch and returned ``None``
    with no state recorded.
    """
    enabled_raw: object = getattr(config.general, "auto_integrate_enabled", True)
    enabled = bool(enabled_raw)
    if not enabled:
        return None
    root = Path(workspace_scope.root)
    # Typed-exception guard: detached HEAD surfaces as
    # ``current_branch is None`` (recorded skip) rather than being
    # absorbed into the broad ``except Exception`` above.
    current_branch: str | None = _current_branch_or_detached_marker(root)
    target: str | None = resolve_integration_target(config, root)
    if target is not None:
        # BEFORE the skip table reads branch_sha: the 'no commits
        # beyond target' and 'on target branch' decisions must be made
        # against the refreshed pointer, never a stale one.
        _refresh_target(config, root, target)

    return root, current_branch, target


def _auto_integrate_check_skip_conditions(
    root: Path,
    current_branch: str,
    target: str,
) -> RebaseState | None:
    """Apply the AC-02 / AC-03 / preconditions skip table; return skip or None.

    A failed HEAD read is recorded with the underlying
    :class:`GitOperationError` message rather than being allowed to
    reach the caller's broad handler, where it became an opaque
    ``unexpected failure`` that named neither the operation nor the
    repository.
    """
    if current_branch == target:
        return _record_skip(reason="on target branch", target=target)
    target_sha = branch_sha(root, target)
    try:
        head_sha = get_head_sha(root)
    except GitOperationError as exc:
        return _record_skip(reason=f"HEAD read failed: {exc}", target=target)
    if target_sha is not None and target_sha == head_sha:
        return _record_skip(reason="no commits beyond target", target=target)
    try:
        check_rebase_preconditions(root)
    except RebasePreconditionError as exc:
        return _record_skip(reason=f"preconditions not met: {exc}", target=target)
    return None


@dataclass(frozen=True)
class _RebaseRunResult:
    """Outcome of :func:`_run_rebase_or_merge`.

    On the happy path ``short_circuit`` is ``None`` and
    ``rebase_outcome`` carries the engine result so the caller can
    build the final :class:`RebaseState`. On a conflict / failure
    that has already been cleared from the durable record,
    ``short_circuit`` carries the :class:`RebaseState` the caller
    should return directly.
    """

    rebase_outcome: RebaseSuccess | RebaseConflicts | RebaseNoOp | RebaseFailed
    merge_attempted: bool
    merge_outcome: MergeResult | None
    short_circuit: RebaseState | None


def _run_rebase_or_merge(
    root: Path,
    target: str,
    conflict_resolver: ConflictResolver | None,
) -> _RebaseRunResult:
    """Drive rebase_onto, fall back to endpoint merge on conflict or failure.

    On success returns a ``_RebaseRunResult`` with ``short_circuit``
    ``None`` and the ``rebase_outcome`` / ``merge_outcome`` the
    caller uses to build the final :class:`RebaseState`. Both a
    conflicted AND a failed rebase fall back to the endpoint merge —
    a rebase that fails for any reason must never end the integration
    attempt while a single three-way merge could still land it. When
    aborting a conflicted rebase leaves it in progress, the durable crash
    record is retained so startup recovery can restore the repository.
    """
    rebase_outcome = rebase_onto(target, repo_root=root)
    if not isinstance(rebase_outcome, (RebaseConflicts, RebaseFailed)):
        return _RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=False,
            merge_outcome=None,
            short_circuit=None,
        )

    return _fallback_to_endpoint_merge(
        root, target, rebase_outcome, conflict_resolver
    )


def _fallback_to_endpoint_merge(
    root: Path,
    target: str,
    rebase_outcome: RebaseConflicts | RebaseFailed,
    conflict_resolver: ConflictResolver | None,
) -> _RebaseRunResult:
    """Abort the unfinished rebase and attempt the endpoint merge (AC-06/AC-07).

    With a ``conflict_resolver``, a conflicted endpoint merge is
    handed to the resolver and — on full resolution — committed
    deterministically, so the integration continues to the
    fast-forward phase instead of giving up (see
    :mod:`ralph.pipeline.auto_integrate_resolve`).
    """
    _abort_rebase_after_conflict(root)
    if rebase_in_progress(root):
        # Keep the pre-mutation record: abort_rebase can fail after the
        # rebase engine has created state, and recovery needs that record
        # to prove ownership and retry the abort/reset on the next run.
        return _RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=False,
            merge_outcome=None,
            short_circuit=_record_conflict(
                reason="rebase in-progress after abort", target=target
            ),
        )

    merge_result = endpoint_merge_with_resolution(
        root, target, conflict_resolver
    )
    if merge_result is None:
        # The merge attempt raised; surface that as the headline
        # conflict state (the merge-attempt reason is more
        # informative than the generic "both conflicted" message).
        _clear_record(root)
        return _RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=True,
            merge_outcome=None,
            short_circuit=_record_conflict(
                reason="rebase conflict followed by merge attempt exception",
                target=target,
            ),
        )
    if merge_result.outcome in ("conflict", RESOLUTION_FAILED):
        _clear_record(root)
        return _RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=True,
            merge_outcome=merge_result,
            short_circuit=_record_rebase_outcome(
                rebase_outcome=rebase_outcome,
                merge_attempted=True,
                merge_outcome=merge_result,
                target=target,
            ),
        )

    # The RebaseConflicts / RebaseFailed marker is preserved so
    # _classify_rebase_outcome can distinguish the AC-06
    # rebase-conflicted-then-cleanly-merged path (headline: merged)
    # from the AC-05 plain-rebase path (headline: rebased). Both
    # conflicted paths return earlier, so this tail is reached only
    # when merge_outcome.outcome is in {"success", "noop"} and the
    # classifier returns the "merged" headline, not "conflict".
    return _RebaseRunResult(
        rebase_outcome=rebase_outcome,
        merge_attempted=True,
        merge_outcome=merge_result,
        short_circuit=None,
    )


def _abort_rebase_after_conflict(root: Path) -> None:
    """Abort a conflicted rebase; never raises."""
    try:
        if rebase_in_progress(root):
            abort_rebase(repo_root=root)
    except Exception as abort_exc:
        logger.warning("auto_integrate: abort_rebase failed: {}", abort_exc)


__all__ = [
    "IntegrationRecord",
    "auto_integrate_after_commit",
    "auto_integrate_on_phase_transition",
    "recover_incomplete_integration",
    "resolve_integration_target",
]
