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
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import (
    branch_exists,
    branch_sha,
    is_ancestor,
    resolve_origin_head_branch,
)
from ralph.git.operations import GitOperationError, get_head_sha
from ralph.git.rebase import (
    RebasePreconditionError,
    check_rebase_preconditions,
)
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate_boundary_refresh import (
    BOUNDARY_REFRESH_THROTTLE,
)
from ralph.pipeline.auto_integrate_budget_seam import (
    carry_budget_through_skip,
    charge_failed_attempt,
    observe_conflict_identity,
)
from ralph.pipeline.auto_integrate_conflict_budget import (
    apply_conflict_budget,
    resolver_allowed,
)
from ralph.pipeline.auto_integrate_context import (
    current_branch_or_detached_marker as _current_branch_or_detached_marker,
)
from ralph.pipeline.auto_integrate_context import (
    record_refresh,
    record_when_stale,
)
from ralph.pipeline.auto_integrate_ff import (
    fast_forward_target,
    is_retryable_fast_forward_failure,
)
from ralph.pipeline.auto_integrate_outcome import (
    ACTION_MERGED as _ACTION_MERGED,
)
from ralph.pipeline.auto_integrate_outcome import (
    record_rebase_outcome as _record_rebase_outcome,
)
from ralph.pipeline.auto_integrate_outcome import (
    record_skip as _record_skip,
)
from ralph.pipeline.auto_integrate_rebase_merge import (
    run_rebase_or_merge as _run_rebase_or_merge,
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

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope


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
# were extracted to :mod:`ralph.pipeline.auto_integrate_record`, and the
# outcome branch table (action verbs, RebaseState builders, rebase/merge
# classifiers) to :mod:`ralph.pipeline.auto_integrate_outcome`, to keep
# this module under the repo-structure ``_MAX_FILE_LINES`` cap. The
# module-level ``from ... import ... as _xxx`` aliases above expose them
# under the original private names so the call sites in this module read
# unchanged.


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
        state: Prior rebase state. Carries the consecutive-conflict
            count and the conflict identity that bound how often the
            dev-agent resolver is invoked for one unresolved conflict.
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
    ran). This is the ONLY seam that can carry another agent's landing
    to an agent that is not committing right now, so its own
    short-circuits are the difference between cross-agent
    synchronisation working and appearing dead.

    It is deliberately quiet: phase boundaries are frequent, so the
    hook returns ``None`` without recording anything when

    * the worktree holds uncommitted TRACKED changes AND the resolved
      target is already contained in ``HEAD`` — a routine mid-phase
      boundary with no catch-up work to lose. When the target DOES
      carry commits this checkout lacks, the same deferral is
      RECORDED as a skip instead, because a suppressed cross-agent
      sync the operator cannot see is indistinguishable from a
      feature that does not work, or
    * the resolved target already sits at the feature tip (nothing to
      rebase, nothing to land) AND the origin refresh that pointer was
      read through was healthy. When the refresh could not confirm the
      pointer (unreachable origin, diverged remote, lost refresh race)
      the same nothing-to-do case is RECORDED as a skip carrying
      ``last_refresh`` instead, because a silent no-op computed from an
      unverifiable pointer is indistinguishable from a healthy one.

    The dirty-boundary ancestry probe is fetch-THROTTLED rather than
    fetch-free. Phase boundaries fire on eleven events, so an
    unconditional fetch on every dirty one is a per-cycle cost
    regression; but a probe that reads a pointer another agent moved
    minutes ago reports "nothing to catch up" from stale data, and that
    silent staleness is indistinguishable from the feature being dead.
    :data:`~ralph.pipeline.auto_integrate_boundary_refresh.BOUNDARY_REFRESH_THROTTLE`
    (default 30.0 s) therefore permits at most one refresh per interval
    per process. The fetch is bounded by
    ``general.auto_integrate_fetch_timeout_seconds`` and fails open: an
    unreachable origin yields ``REFRESH_UNREACHABLE`` and integration
    continues against the local ref. In the linked-worktree topology
    this feature exists for, ``refs/heads/<target>`` is shared across
    every agent, so the local ref IS the authoritative pointer and the
    refresh correctly records ``REFRESH_NO_ORIGIN``.

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
            return _defer_dirty_boundary(config, root, target)
        # A stale remote pointer must not let this cheap hook conclude
        # 'nothing to do'. Every free early return above still costs
        # nothing.
        refresh = _refresh_target(config, root, target)
        target_sha = branch_sha(root, target)
        if target_sha is not None and target_sha == get_head_sha(root):
            # Fully integrated and landed: the frequent-boundary case.
            # Quiet only while the refreshed pointer that verdict was
            # read through can be trusted (see ``record_when_stale``).
            return record_when_stale(
                _record_skip(reason="no commits beyond target", target=target),
                refresh,
            )
    except Exception as exc:
        logger.warning(
            "auto_integrate: phase-transition pre-check failed: {}", exc
        )
        return None
    return auto_integrate_after_commit(
        config, workspace_scope, state, conflict_resolver=conflict_resolver
    )


def _worktree_is_clean(root: Path) -> bool:
    """True when no uncommitted TRACKED modification is present.

    Uses the SAME definition of "clean" as
    :func:`ralph.git.rebase.rebase_preconditions._ensure_clean_worktree`
    (``git status --porcelain --untracked-files=no``), and for the same
    reason its docstring records: blocking on untracked files "turned a
    per-file, git-detectable hazard into a run-wide outage: one scratch
    file left by a phase disabled integration for every later commit
    seam".

    This guard used to be the one asymmetric holdout, and it sits on the
    only seam that carries ANOTHER agent's landing to an agent that is
    not committing right now. So the asymmetry re-created exactly that
    outage on the seam where it hurts most: a single stray scratch file
    silently disabled cross-agent synchronisation for the rest of the
    run.

    Untracked work in flight is still safe: ``git rebase`` and
    ``git merge`` refuse non-destructively and per-file for any
    untracked path they would overwrite, and that refusal already routes
    into the endpoint-merge fallback via
    :func:`ralph.pipeline.auto_integrate_rebase_merge.run_rebase_or_merge`.
    Uncommitted TRACKED modifications still defer the boundary.

    Fails closed (False) on any git failure so the phase-transition
    hook never integrates on top of a worktree it cannot prove clean.
    """
    result = run_git(
        ("status", "--porcelain", "--untracked-files=no"),
        cwd=root,
        label="git-transition-status",
    )
    if result.returncode != 0:
        return False
    return not result.stdout.strip()


def _defer_dirty_boundary(
    config: UnifiedConfig, root: Path, target: str
) -> RebaseState | None:
    """Defer a boundary integration, recording it only when it cost something.

    A dirty boundary is routine and fires on eleven phase-transition
    events, so it stays an INFO log line and returns ``None`` when the
    resolved target is already contained in ``HEAD``: nothing was lost.

    When the target carries commits this checkout LACKS, the deferral
    suppressed a genuine cross-agent catch-up, and a suppression the
    operator cannot see is the whole reason auto-integration reads as
    broken. That case is recorded so it surfaces in the
    ``auto-integrate:`` line, carrying the ``REFRESH_*`` outcome so the
    operator can also see how the pointer it was decided from was read.

    The ancestry probe runs against a THROTTLED refresh rather than a
    fetch-free local read: see
    :func:`auto_integrate_on_phase_transition` for the interval, the
    bound and the fail-open behaviour.
    """
    refresh = (
        _refresh_target(config, root, target)
        if BOUNDARY_REFRESH_THROTTLE.should_refresh()
        else None
    )
    target_sha = branch_sha(root, target)
    if target_sha is not None and not is_ancestor(root, target_sha, get_head_sha(root)):
        skip = _record_skip(
            reason=(
                "worktree not clean; uncommitted tracked changes deferred "
                "catch-up integration"
            ),
            target=target,
        )
        return record_refresh(skip, refresh)
    logger.info(
        "auto_integrate: phase-transition integration deferred; "
        "worktree dirty (target '{}')",
        target,
    )
    return None


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
    # The refresh outcome every decision below is made against. Carried
    # down instead of discarded so the conflict and short-circuit
    # records say how fresh the mainline pointer was too -- the success
    # path was previously the only one that could (AC-04).
    refresh: str | None = ctx[3] if ctx is not None else None
    early_skip, usable_ctx = _check_early_skips(ctx)
    if early_skip is not None:
        return carry_budget_through_skip(early_skip, prior=state)
    if usable_ctx is None:
        # Disabled (AC-01) or env lookup failed: caller already
        # recorded the skip when applicable.
        return None
    root, _current_branch, target = usable_ctx

    # Budget check BEFORE any git mutation: an exhausted budget still
    # runs the rebase and the endpoint merge (and still aborts them
    # cleanly), it merely stops paying for another agent invocation.
    # The identity is observed here, before the rebase moves anything,
    # so it names the endpoints this attempt is about to reconcile.
    identity = observe_conflict_identity(root, target)
    allowed = resolver_allowed(state, target, identity)
    effective_resolver = conflict_resolver if allowed else None
    resolver_suppressed = conflict_resolver is not None and not allowed
    if resolver_suppressed:
        logger.warning(
            "auto_integrate: conflict resolution budget exhausted for '{}'; "
            "not invoking the resolver again until an integration lands",
            target,
        )

    record: RebaseState | None = None
    prefer_merge = False
    try:
        for attempt in range(_MAX_INTEGRATION_ATTEMPTS):
            if attempt:
                # A retry only happens because the target moved under
                # us: re-read it from origin (AC-03) and re-observe the
                # identity, so a conflict recorded by a later attempt
                # is not stamped with attempt 0's endpoint pair.
                refresh = _refresh_target(config, root, target)
                identity = observe_conflict_identity(root, target)
            record, retry_ff = _integrate_once(
                config,
                root,
                target,
                effective_resolver,
                prefer_merge=prefer_merge,
                refresh=refresh,
            )
            if not retry_ff:
                break
            # Only a merge-producing attempt suppresses the next
            # rebase: a plain ``git rebase`` carries no merge commits,
            # so replaying over the merge this attempt just created
            # would discard a resolution an agent was paid up to
            # ``auto_integrate_resolve_timeout_seconds`` to produce and
            # walk straight back into the same conflict. A clean
            # rebase-only attempt still retries as a rebase and keeps
            # the history linear.
            prefer_merge = record is not None and record.last_action == _ACTION_MERGED
            logger.info(
                "auto_integrate: fast-forward did not land on attempt {}; "
                "re-integrating onto the moved target",
                attempt + 1,
            )
    except Exception as exc:
        # Caught here, not only in the caller's broad guard: this is
        # the first frame knowing the target/identity to scope by.
        logger.warning("auto_integrate: integration attempt failed: {}", exc)
        with contextlib.suppress(Exception):
            _clear_record(root)
        return charge_failed_attempt(
            record_refresh(
                _record_skip(reason=f"unexpected failure: {exc}", target=target),
                refresh,
            ),
            prior=state,
            target=target,
            identity=identity,
            resolver_offered=effective_resolver is not None,
        )
    if record is None:
        return None
    return apply_conflict_budget(
        record,
        prior=state,
        target=target,
        resolver_suppressed=resolver_suppressed,
        identity=identity,
    )


def _integrate_once(
    config: UnifiedConfig,
    root: Path,
    target: str,
    conflict_resolver: ConflictResolver | None,
    *,
    prefer_merge: bool = False,
    refresh: str | None = None,
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

    ``prefer_merge`` is set by the retry loop when the PREVIOUS attempt
    produced a merge commit; it routes this pass straight to the
    endpoint merge so that merge commit is preserved rather than
    dropped by a non-``--rebase-merges`` rebase.

    ``refresh`` is the ``REFRESH_*`` outcome the caller resolved the
    target pointer through. It is stamped onto every short-circuit
    record this pass returns; the success path uses the FRESHER
    pre-landing refresh instead.
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

    rebase_result = _run_rebase_or_merge(
        root, target, conflict_resolver, prefer_merge=prefer_merge
    )
    if rebase_result.short_circuit is not None:
        # Resolved failures clear the record; an abort that leaves a rebase
        # in progress retains it for startup recovery.
        return record_refresh(rebase_result.short_circuit, refresh), False

    # Success path: the feature branch contains the target.
    feature_sha = _read_post_integration_head_sha(root, target)
    if feature_sha is None:
        return record_refresh(
            _record_skip(
                reason="post-integration HEAD read failed", target=target
            ),
            refresh,
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
    ctx: tuple[Path, str | None, str | None, str | None] | None,
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
    root, current_branch, target, refresh = ctx
    if current_branch is None:
        # Detached HEAD: AC-02 requires this to be a RECORDED skip,
        # not a silent no-op. The crash record is never written in
        # this branch -- a detached HEAD is not an in-progress
        # integration.
        return record_refresh(
            _record_skip(reason="detached HEAD", target=target), refresh
        ), None
    if target is None:  # AC-13: no target resolved -> recorded skip
        return _record_skip(
            reason="no integration target branch resolved", target=None
        ), None
    skip = _auto_integrate_check_skip_conditions(root, current_branch, target)
    if skip is not None:
        # Every skip in that table is decided FROM the target pointer
        # the refresh above was meant to freshen, so the record carries
        # how fresh that pointer actually was.
        return record_refresh(skip, refresh), None
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


def _auto_integrate_resolve_context(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
) -> tuple[Path, str | None, str | None, str | None] | None:
    """Resolve the (root, current_branch, target, refresh) context or short-circuit.

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
    refresh: str | None = None
    if target is not None:
        # BEFORE the skip table reads branch_sha: the 'no commits
        # beyond target' and 'on target branch' decisions must be made
        # against the refreshed pointer, never a stale one. The outcome
        # is carried out of here rather than discarded: those skip
        # decisions inherit whatever staleness the refresh could not
        # rule out, and the fail-open contract means nothing else will
        # report it.
        refresh = _refresh_target(config, root, target)

    return root, current_branch, target, refresh


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


__all__ = [
    "IntegrationRecord",
    "auto_integrate_after_commit",
    "auto_integrate_on_phase_transition",
    "recover_incomplete_integration",
    "resolve_integration_target",
]
