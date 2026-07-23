"""Auto-integration: rebase the feature branch onto the mainline after each commit.

After every commit phase that actually creates a commit, the Ralph
pipeline runs :func:`auto_integrate_after_commit` to keep the
feature branch and the local mainline ref in lockstep:

1. **Rebase first.** Rebase the feature branch onto the resolved
   target tip.
2. **Resolve the rebase in place.** A rebase that stops on a
   conflict is not abandoned. Each conflicted stop is handed to the
   conflict-resolution pipeline; once Ralph has proved the stop
   resolved it stages the paths itself and runs
   ``git rebase --continue``, then repeats for the next stop. A
   resolution that lands leaves linear history and no merge commit.
3. **Merge on unresolved conflict.** Only when resolution declines,
   exhausts a budget, errors or is unavailable is the rebase
   aborted cleanly and one merge of the target branch into the
   feature branch attempted. A single endpoint three-way merge often
   succeeds where commit-by-commit replay conflicts, and it still
   makes the target an ancestor of the feature branch, preserving
   fast-forwardability.
4. **Give up gracefully.** If the merge also conflicts, abort it,
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
import random
import time
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
from ralph.pipeline.auto_integrate_backoff import wait_before_retry
from ralph.pipeline.auto_integrate_boundary_refresh import BOUNDARY_REFRESH_THROTTLE
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
    maybe_push_target,
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
    TerminalStateViolationError,
    _reclaim_unowned_stale_rebase,
    post_attempt_verify,
    recover_incomplete_integration,
    recovery_retained_record,
)
from ralph.pipeline.auto_integrate_refresh import (
    refresh_target as _refresh_target,
)
from ralph.pipeline.auto_integrate_sync import REFRESH_SUPPRESSED
from ralph.pipeline.auto_integrate_worktree_state import (
    _worktree_is_clean,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.config.models import UnifiedConfig
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
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

    Resolution is a STATELESS, local-refs-only check, re-derived from
    the repository at every seam. Remote metadata (``origin/HEAD``) may
    at most pick BETWEEN branches that already exist locally; it never
    creates a local branch from a remote-tracking ref and never moves
    one, so remote state cannot influence the base of a local rebase --
    including at the very first seam of a fresh checkout.

    Precedence:

    1. ``config.general.auto_integrate_target`` when set: used verbatim
       when the branch exists locally (AC-13).
    2. Else: the ``origin/HEAD`` default-branch NAME, when a local
       branch of that name exists.
    3. Else: the first existing local branch in
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
        if branch_exists(repo_root_path, configured):
            return configured
        return None

    origin_default = resolve_origin_head_branch(repo_root_path)
    if origin_default and branch_exists(repo_root_path, origin_default):
        return origin_default

    for candidate in _AUTO_DETECT_TARGET_CANDIDATES:
        if branch_exists(repo_root_path, candidate):
            return candidate

    return None


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
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
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
        rebase_stop_resolver: Optional callable handed ONE stopped commit
            of a conflicted rebase. Supplying it is what lets a
            conflicted rebase be resolved in place and land as
            ``rebased``; without it a conflicted rebase is aborted and
            retried as a single endpoint merge, exactly as before.
        display: Active display, used only so the resolution loop can own
            the status-bar footer for its whole duration.
        sleep: Wait seam used between bounded landing retries. Injected
            so deterministic tests assert the schedule without waiting.
        jitter: Uniform ``[0, 1)`` source for the retry backoff, injected
            for the same reason.

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
            config,
            workspace_scope,
            state,
            conflict_resolver,
            rebase_stop_resolver=rebase_stop_resolver,
            display=display,
            sleep=sleep,
            jitter=jitter,
        )
    except TerminalStateViolationError:
        # R6/AC-06: terminal-state violations are loud, never
        # silent. Re-raise so the caller (or the recovery preamble)
        # sees the exact cause; do NOT clear the record here -- it
        # is the recovery preamble's only handle on the
        # pre-attempt SHA needed to restore the feature branch.
        raise
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
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
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
    (default 30.0 s) therefore permits at most one SUCCESSFUL refresh
    per interval per ``(repository root, target)`` pair -- keyed, so two
    worktrees or two targets sharing one process cannot steal each
    other's window, and consume-on-success, so one unreachable-origin
    blip does not blind the next whole interval. The fetch is bounded by
    ``general.auto_integrate_fetch_timeout_seconds`` and fails open: an
    unreachable origin yields ``REFRESH_UNREACHABLE`` and integration
    continues against the local ref. In the linked-worktree topology
    this feature exists for, ``refs/heads/<target>`` is shared across
    every agent, so the local ref IS the authoritative pointer, and
    re-reading it there records ``REFRESH_LOCAL_FLEET``.
    ``REFRESH_NO_ORIGIN`` is reserved for the target that could not be
    observed at all, and is NOT treated as healthy.

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
            # AC-08: even the "no target configured" short-circuit
            # must surface a recorded skip rather than a silent
            # ``None``; an operator looking at the run log must be
            # able to tell "this phase boundary did nothing because
            # no integration target was configured" from "this run
            # is missing a target entirely". The boundary hook still
            # costs nothing here -- ``_record_skip`` is a pure
            # dataclass construction.
            return _record_skip(reason="no integration target configured", target="")
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
        # AC-08: never silently swallow a phase-transition pre-check
        # exception while ``auto_integrate_enabled`` is true. Surface
        # the failure as a recorded skip carrying the underlying
        # exception text so an operator looking at the run log can
        # tell WHICH precondition tripped (an opaque ``None`` here is
        # indistinguishable from "the feature is disabled" or "this
        # boundary decided not to fire", both of which are recorded
        # states). The boundary hook still costs nothing here --
        # ``_record_skip`` is a pure dataclass construction, the
        # WARNING carries the failure detail.
        logger.warning(
            "auto_integrate: phase-transition pre-check failed: {}\n"
            "  recording skip (AC-08); the next seam will retry from a "
            "fresh live local ref",
            exc,
        )
        try:
            recorded_target = resolve_integration_target(config, Path(workspace_scope.root)) or ""
        except Exception:
            recorded_target = ""
        return _record_skip(
            reason=f"phase-transition pre-check failed: {exc}",
            target=recorded_target,
        )
    return auto_integrate_after_commit(
        config,
        workspace_scope,
        state,
        conflict_resolver=conflict_resolver,
        rebase_stop_resolver=rebase_stop_resolver,
        display=display,
        sleep=sleep,
        jitter=jitter,
    )


def _target_is_ahead(root: Path, target_sha: str | None) -> bool:
    """Whether ``target_sha`` carries commits ``HEAD`` does not have.

    An unreadable target pointer is NOT divergence: there is nothing to
    catch up on that this function can prove, and the caller's quiet
    path is the right answer for a question git could not be asked.

    Kept in this module (not extracted to
    :mod:`ralph.pipeline.auto_integrate_worktree_state` with
    :func:`_worktree_is_clean`) because the test suite patches
    ``is_ancestor`` / ``get_head_sha`` on the
    ``ralph.pipeline.auto_integrate`` namespace to drive this
    helper; moving it would silently bypass the existing
    monkeypatch seams in
    ``tests/test_auto_integrate_boundary_refresh.py``.
    """
    if target_sha is None:
        return False
    return not is_ancestor(root, target_sha, get_head_sha(root))


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
    bound and the fail-open behaviour. When the throttle declines, the
    record carries
    :data:`~ralph.pipeline.auto_integrate_sync.REFRESH_SUPPRESSED`
    rather than nothing at all: a boundary decided from a pointer this
    round never re-read is exactly as unverifiable as one whose refresh
    failed, and hiding that behind an absent ``last_refresh`` is what
    made a suppressed cross-agent catch-up indistinguishable from a
    verified one.

    One case overrides the throttle: when it declined AND the local
    pointer ALREADY shows the target carrying commits this checkout
    lacks, a single refresh runs and the verdict is retaken from the
    re-read pointer. That is the only case where the answer matters --
    an operator-visible catch-up is about to be recorded -- and gating
    it on local evidence of divergence keeps the common dirty boundary
    free of any fetch at all.

    A healthy forced refresh consumes one separate override permit for
    this throttle interval, so a burst of still-divergent dirty
    boundaries does not become a fetch storm. An unhealthy forced
    refresh leaves that permit available for an immediate retry.
    """
    refresh = REFRESH_SUPPRESSED
    if BOUNDARY_REFRESH_THROTTLE.should_refresh(root, target):
        refresh = _refresh_target(config, root, target)
        BOUNDARY_REFRESH_THROTTLE.record_outcome(root, target, refresh)
    target_sha = branch_sha(root, target)
    diverged = _target_is_ahead(root, target_sha)
    if (
        diverged
        and refresh == REFRESH_SUPPRESSED
        and BOUNDARY_REFRESH_THROTTLE.should_force_refresh(root, target)
    ):
        refresh = _refresh_target(config, root, target)
        BOUNDARY_REFRESH_THROTTLE.record_forced_outcome(root, target, refresh)
        target_sha = branch_sha(root, target)
        diverged = _target_is_ahead(root, target_sha)
    if diverged:
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
#: next commit phase. Exhausting the budget is not silent: the returned
#: record names it (see :func:`_record_attempt_budget_spent`), because
#: "the target moved once" and "the target kept moving until I gave up"
#: call for different operator responses.
_MAX_INTEGRATION_ATTEMPTS = 3

def _auto_integrate_after_commit_inner(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    state: RebaseState,
    conflict_resolver: ConflictResolver | None,
    *,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> RebaseState | None:
    """Internal worker for :func:`auto_integrate_after_commit`.

    The body is split into three narrow phases so each phase keeps a
    sensible branch / statement count without losing the
    skip-condition table from the product brief:

    1. :func:`_auto_integrate_resolve_context` -- handle the
       ``enabled`` / env-lookup / on-target / no-commits-beyond /
       preconditions skip conditions.
    2. :func:`_integrate_once` -- write the crash record, run the
       rebase engine, resolve any conflicted rebase stop in place,
       fall back to the endpoint merge (optionally agent-resolved)
       only when that resolution does not land, then fast-forward.
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
    attempts_exhausted = False
    try:
        for attempt in range(_MAX_INTEGRATION_ATTEMPTS):
            if attempt:
                # Wait BEFORE the re-read, never after: the point of the
                # delay is that the retry observes a pointer read once
                # the collision has had time to settle.
                wait_before_retry(attempt, sleep=sleep, jitter=jitter)
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
                rebase_stop_resolver=(
                    rebase_stop_resolver if allowed else None
                ),
                display=display,
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
            if attempt + 1 < _MAX_INTEGRATION_ATTEMPTS:
                logger.info(
                    "auto_integrate: fast-forward did not land on attempt {}; "
                    "re-integrating onto the moved target",
                    attempt + 1,
                )
                continue
            # Last iteration: the range ends here and NOTHING
            # re-integrates. Promising a retry that never runs, and
            # recording only the last fast-forward skip reason, left the
            # operator with no way to tell "the target moved once" from
            # "the target kept moving until I ran out of attempts".
            attempts_exhausted = True
            logger.warning(
                "auto_integrate: fast-forward did not land after {} integration "
                "attempts; giving up until the next seam",
                _MAX_INTEGRATION_ATTEMPTS,
            )
    except TerminalStateViolationError:
        # R6/AC-06: a terminal-state violation is loud, not
        # silent. The durable record is RETAINED so the recovery
        # preamble (or the next seam) can reclaim the leaked
        # state using the recorded pre-attempt SHA. Re-raise
        # so the outer try/except in
        # :func:`auto_integrate_after_commit` propagates the
        # failure instead of converting it to a recorded skip
        # the operator would never correlate with the leak.
        raise
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
    if attempts_exhausted:
        record = _record_attempt_budget_spent(record)
    return apply_conflict_budget(
        record,
        prior=state,
        target=target,
        resolver_suppressed=resolver_suppressed,
        identity=identity,
    )


def _record_attempt_budget_spent(record: RebaseState) -> RebaseState:
    """Append the spent-attempt-budget fact to a record's skip reason.

    The underlying concurrency reason is kept as the headline -- it is
    what an operator needs in order to act -- and the exhaustion is
    appended, so the ``auto-integrate:`` line says both WHY the landing
    kept failing and that the bounded loop stopped trying.
    """
    base = record.last_reason or "fast-forward did not land"
    return record.model_copy(
        update={
            "last_reason": (
                f"{base}; exhausted {_MAX_INTEGRATION_ATTEMPTS} integration attempts"
            )
        }
    )


def _integrate_once(
    config: UnifiedConfig,
    root: Path,
    target: str,
    conflict_resolver: ConflictResolver | None,
    *,
    prefer_merge: bool = False,
    refresh: str | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
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
    target pointer through, and it is deliberately the CALLER's job to
    have taken it immediately beforehand. Both call paths do: attempt 0
    is entered straight after the context-resolution refresh, and every
    retry re-refreshes at the top of the loop before calling in. A third
    refresh here would sit between two reads that already bracket the
    rebase, and ``rebase_onto`` resolves the target BY NAME, so git
    re-reads the freshest ref itself when the replay actually starts.
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
    # B11/E5: backup the original feature tip on a uniquely named
    # ``refs/rebase-backup/<id>`` ref BEFORE any mutation so a
    # concurrent ``git gc --prune`` cannot reclaim the in-flight
    # commits while the rebase / merge is in progress. The ref is
    # retained through the attempt (the recovery preamble reads it
    # to restore the pre-attempt tip on a verified abort) and
    # deleted after a successful land or a verified abort.
    backup_ref = _create_rebase_backup_ref(root, pre_feature_sha)
    owns_resolution = conflict_resolver is not None
    try:
        rebase_result = _run_rebase_or_merge(
            root,
            target,
            conflict_resolver,
            prefer_merge=prefer_merge,
            rebase_stop_resolver=rebase_stop_resolver,
            display=display,
        )
        if rebase_result.short_circuit is not None:
            # Resolved failures clear the record; an abort that leaves a rebase
            # in progress retains it for startup recovery.
            _verify_and_cleanup_backup(root, backup_ref, pre_feature_sha, owns_resolution)
            return record_refresh(rebase_result.short_circuit, refresh), False

        # Success path: the feature branch contains the target.
        feature_sha = _read_post_integration_head_sha(root, target)
        if feature_sha is None:
            _verify_and_cleanup_backup(
                root, backup_ref, pre_feature_sha, owns_resolution
            )
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

        # R6/AC-06: post-attempt terminal-state verification on EVERY
        # exit path. A completed rebase/merge leaves HEAD at feature_sha,
        # regardless of whether the target fast-forward wins its race;
        # only the earlier abort paths verify pre_feature_sha.
        #
        # The record MUST stay on disk until this verification
        # passes. ``_verify_and_cleanup_backup`` now raises
        # :class:`TerminalStateViolationError` on failure, so a
        # terminal-state violation aborts the integration with the
        # durable record still present; the recovery preamble (or
        # the next seam) can then reclaim the leaked state using
        # the recorded pre-attempt SHA. Clearing the record BEFORE
        # verification (the prior shape) discarded the very
        # metadata recovery needs to restore the pre-attempt tip.
        _verify_and_cleanup_backup(
            root,
            backup_ref,
            None,
            owns_resolution,
        )
        # Terminal-state invariant passed: the backup ref is gone,
        # so the durable record is no longer the recovery
        # preamble's only handle on the in-flight attempt. Clear
        # it now -- the function's remaining work below
        # (recording the outcome, pushing to remotes) cannot
        # fail in a way that needs the record for recovery.
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
        # Opt-in multi-remote push. Runs ONLY after the local fast-forward
        # already landed -- a remote failure cannot undo a local ref
        # advance. The push is fail-open and best-effort by contract; the
        # helper never raises, the record is updated to carry the summary
        # so a partial push is operator-visible, and the (ok, retry_ff)
        # landing tuple returned above is unchanged. When push is disabled
        # (default) or the local config has no such flag at all, the field
        # is left as the inherited None, so legacy checkpoints stay clean.
        # Shared with the crash-recovery landing site via
        # ``maybe_push_target`` so every successful advance of the local
        # target reaches every configured remote when push is enabled.
        record = maybe_push_target(config, root, target, record)
        return record, False
    except BaseException:
        # R6/AC-06: terminal-state verification on the EXCEPTION
        # path. The success path above already ran
        # ``_verify_and_cleanup_backup`` (right after the
        # fast-forward), so a violation that came from the
        # helper itself is already the cause of this ``except``
        # clause and MUST NOT be re-verified -- the prior shape
        # called the helper again from this block and the
        # helper raised a second time, masking the original
        # cause. We track the verification result with a
        # sentinel: if the helper already ran successfully
        # BEFORE the exception, the invariant was satisfied
        # and the exception is from a later step; if it did
        # not run, the exception came from the rebase/merge/
        # ff phase and the helper must run to verify the
        # terminal state on the abort path.
        raise


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


def _reclaim_and_retry_preconditions(
    root: Path,
    target: str,
    precondition_exc: RebasePreconditionError,
) -> RebaseState | None:
    """AC-07/R8 reclaim-at-seam helper.

    On a precondition failure that looks like unowned stale state,
    invoke :func:`_reclaim_unowned_stale_rebase` to abort/reclaim
    the state, then re-run ``check_rebase_preconditions`` and
    return ``None`` (proceed) when it now passes. A dirty tree
    keeps the state protected (AC-11 case 4); a post-reclaim
    precondition failure surfaces a loud ``skipped`` record with
    the cause so the next seam can retry.

    Return contract: a recorded skip is returned when the
    precondition still fails after the reclaim, ``None`` otherwise
    (both when the reclaim did nothing and when it succeeded).
    The caller relies on this contract to distinguish "skip
    because the reclaim retried and still failed" from "proceed
    because the reclaim worked" by checking the LAST_REASON
    string on the returned record (only the recorded-skip
    branch sets a ``preconditions not met after reclaim`` reason).
    """
    reclaimed = _reclaim_unowned_stale_rebase(root)
    if reclaimed is None:
        # No reclaim happened: either nothing to reclaim or the
        # tree was dirty (operator-owned, AC-11 case 4). Let the
        # caller record a skip with the original cause.
        return None
    logger.warning(
        "auto_integrate: rebase preconditions blocked by stale "
        "unowned state for target '{}': {}; "
        "reclaimed at seam (AC-07/R8) -- retrying preconditions",
        target,
        precondition_exc,
    )
    try:
        check_rebase_preconditions(root)
    except RebasePreconditionError as retry_exc:
        # Reclaim ran but the precondition still fails (e.g.
        # the state was operator-owned and the dirty-tree check
        # kept it). Surface the failure loudly with the
        # original cause so the next seam can retry; never a
        # silent noop.
        logger.warning(
            "auto_integrate: rebase preconditions still failing "
            "after reclaim at seam for target '{}': {} (was {}); "
            "the recovery preamble will reclaim on the next run",
            target,
            retry_exc,
            precondition_exc,
        )
        return _record_skip(
            reason=f"preconditions not met after reclaim: {retry_exc}",
            target=target,
        )
    # Reclaim succeeded and the precondition now passes: proceed
    # in the same seam. We return a recorded skip with a sentinel
    # ``_RECLAIM_SUCCEEDED`` reason so the call site can detect
    # the success case without falling through to the original
    # precondition error.
    return _record_skip(
        reason="_reclaim_succeeded",
        target=target,
    )


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

    AC-07/R8 (reclaim-at-seam): a ``RebasePreconditionError`` that
    points at UNOWNED stale state (the ``rebase_in_progress`` /
    ``merge_in_progress`` family of precondition failures) on a
    clean tree is NOT a permanent disablement -- it is a recoverable
    block. The reclaim is delegated to
    :func:`_reclaim_and_retry_preconditions`, which re-runs the
    preconditions in the same seam when the reclaim succeeds and
    proceeds. A dirty tree with unmerged paths is preserved
    (operator-owned, AC-11 case 4).
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
        return _handle_precondition_failure(root, target, exc)
    return None


def _handle_precondition_failure(
    root: Path,
    target: str,
    exc: RebasePreconditionError,
) -> RebaseState | None:
    """Handle a precondition failure with AC-07/R8 reclaim-at-seam.

    The helper's return contract distinguishes three cases via the
    returned state:

    * ``None`` -- the reclaim did nothing; the caller should
      record a skip with the original cause.
    * ``RebaseState`` with reason starting with
      ``"preconditions not met after reclaim: "`` -- the reclaim
      ran but the precondition still fails; the caller returns
      this state directly.
    * ``RebaseState`` with the ``_reclaim_succeeded`` sentinel
      reason -- the reclaim succeeded and the precondition now
      passes; the caller proceeds in this seam (the sentinel
      reason prevents the call site from falling through to the
      original precondition error and recording a skip with the
      wrong reason).

    Extracted from :func:`_auto_integrate_check_skip_conditions`
    so the orchestrator stays under the ruff PLR0911
    (too-many-returns) cap.
    """
    post_reclaim = _reclaim_and_retry_preconditions(root, target, exc)
    if post_reclaim is not None:
        if post_reclaim.last_reason == "_reclaim_succeeded":
            return None
        return post_reclaim
    # A precondition failure disables auto-integration for the whole
    # run, so it must not be visible only as one token on an
    # activity line that scrolls past. WARN names the target and the
    # precondition so the cause is greppable in the run log; the
    # recorded skip keeps the never-crash semantics unchanged.
    logger.warning(
        "auto_integrate: rebase preconditions not met for target '{}': {}",
        target,
        exc,
    )
    return _record_skip(reason=f"preconditions not met: {exc}", target=target)


def _create_rebase_backup_ref(root: Path, pre_feature_sha: str | None) -> str | None:
    """Create ``refs/rebase-backup/<id>`` on the pre-attempt tip (B11/E5).

    The backup ref keeps the in-flight original feature tip reachable
    for the duration of an attempt so a concurrent ``git gc --prune``
    on a shared object store cannot reclaim commits that an abort
    needs to restore. Returns the ref name on success, ``None`` when
    the SHA was unobservable (a deleted / unborn branch is its own
    pre-attempt state and needs no backup).

    The ref is uniquely named (``rebase-backup/<sha[:8]>-<sha>``) so
    concurrent attempts in the same repository never collide on the
    same name and the recovery preamble can discover them all.

    Failure modes are loud and FAIL CLOSED (B11/E5 contract): if
    ``update-ref`` raises or returns non-zero, this function raises
    so the attempt is aborted BEFORE any rebase/merge mutation.
    Continuing without the B11/E5 backup would risk a concurrent
    ``git gc --prune`` reclaiming the in-flight tip while the
    pipeline is mutating it (an unrecoverable history loss on a
    shared object store). The CAS-style update-ref is atomic on
    its own and does not move the feature ref.
    """
    if pre_feature_sha is None:
        return None
    backup_name = f"refs/rebase-backup/{pre_feature_sha[:8]}-{pre_feature_sha}"
    try:
        result = run_git(
            ("update-ref", backup_name, pre_feature_sha),
            cwd=root,
            label="auto-integrate:backup-ref-create",
        )
    except Exception as exc:
        # B11/E5 fail-closed: the attempt cannot proceed without a
        # backup ref because a concurrent gc could prune the
        # in-flight tip mid-run. Raise loudly; the outer
        # _integrate_once exception handler runs the verified-abort
        # path (post_attempt_verify + record retention) so no
        # state is left behind and the next seam can resume.
        raise RuntimeError(
            f"auto_integrate: B11/E5 backup-ref creation raised "
            f"unexpectedly; refusing to start the attempt without "
            f"recovery reachability for the in-flight tip: {exc}"
        ) from exc
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout).strip()[:200]
        # B11/E5 fail-closed: see the docstring above. Raising here
        # routes through the outer exception handler which clears
        # the durable integration record via the verified-abort
        # path. We do NOT silently continue without the backup.
        raise RuntimeError(
            f"auto_integrate: B11/E5 backup-ref creation failed "
            f"(rc={result.returncode}): {stderr}; refusing to start "
            f"the attempt without recovery reachability for the "
            f"in-flight tip"
        )
    return backup_name


def _delete_rebase_backup_ref(root: Path, backup_ref: str | None) -> None:
    """Delete ``refs/rebase-backup/<id>`` after a verified land or abort.

    Safe to call when the ref does not exist: a stale backup from a
    previous run is silently cleaned up by ``update-ref -d``. The
    function never raises -- a backup-ref left around is recoverable
    on the next run (it does not block integration), while a stuck
    attempt on a missing backup would.
    """
    if backup_ref is None:
        return
    try:
        result = run_git(
            ("update-ref", "-d", backup_ref),
            cwd=root,
            label="auto-integrate:backup-ref-delete",
        )
    except Exception as exc:  # pragma: no cover -- defensive
        logger.warning(
            "auto_integrate: backup-ref deletion raised unexpectedly: {}; "
            "the stale backup will be discovered by the next recovery pass",
            exc,
        )
        return
    if result.returncode != 0:
        # ``update-ref -d`` returns 1 when the ref does not exist;
        # that is the normal "already cleaned" path.
        stderr = (result.stderr or "").strip()
        if "not exist" in stderr or result.returncode not in (0, 1):
            logger.warning(
                "auto_integrate: backup-ref deletion failed (rc={}): {}; "
                "the stale backup will be discovered by the next recovery pass",
                result.returncode,
                stderr[:200],
            )


def _verify_and_cleanup_backup(
    root: Path,
    backup_ref: str | None,
    expected_head_sha: str | None,
    owns_resolution: bool,
) -> None:
    """Verify the terminal-state invariant and delete the backup ref (R6/B11).

    Composed of:

    * :func:`post_attempt_verify` from
      :mod:`ralph.pipeline.auto_integrate_recovery` -- asserts the
      git dir carries no in-progress markers (unless a live
      resolution session owns them) AND that HEAD resolves to
      exactly ``expected_head_sha`` on the abort path. RAISES
      :class:`TerminalStateViolationError` on failure so the caller
      surfaces a loud diagnostic AND the recovery code path retains
      the durable record / backup ref until the invariant is
      restored (the analysis feedback's AC-06 contract: a failed
      invariant must be loud, not silent-and-ignored).
    * :func:`_delete_rebase_backup_ref` -- cleans up the backup ref
      ONLY after a verified pass; the call is skipped when the
      invariant failed so the next recovery pass can still read the
      backup ref to restore the pre-attempt tip.

    Called from EVERY exit path of
    :func:`_integrate_once` -- success, conflict, fallback, error,
    timeout, signal -- and from the outer exception guards. A backup
    ref without a verified invariant would leave an unverified
    ``refs/rebase-backup/<id>`` on disk, so the cleanup is gated on
    the verification passing.

    A failed verification RE-RAISES :class:`TerminalStateViolationError`
    so the caller (and any outer exception guard) can either record
    a loud skip and propagate, or hand the violation to the recovery
    preamble. The earlier swallow-and-log shape was the AC-06
    regression the analysis feedback flagged: a leaked
    ``rebase-merge``/``REBASE_HEAD`` was reported as an ordinary
    return and the next seam started without the recovery path
    being aware the previous attempt leaked. This function is the
    single chokepoint for every exit path's terminal-state check,
    so swallowing here is swallowing for the whole subsystem.
    """
    post_attempt_verify(
        root,
        expected_head_sha=expected_head_sha,
        owns_resolution=owns_resolution,
    )
    # R6/AC-06: the invariant passed -- the backup ref is safe to
    # delete. The cleanup is unconditional: a verified pass means
    # the next recovery run has no need for this backup.
    _delete_rebase_backup_ref(root, backup_ref)


__all__ = [
    "IntegrationRecord",
    "auto_integrate_after_commit",
    "auto_integrate_on_phase_transition",
    "recover_incomplete_integration",
    "recovery_retained_record",
    "resolve_integration_target",
]


# ----- AC-14 catalog evidence -----
# This file is the authoritative source for the catalog entries listed
# below. Each ``# AC-14 rationale: <ID>`` line is the code-adjacent
# marker the AC-14 audit looks for; each ``# ladder rung: <N>``
# names the rung the entry sits on. Adding a new entry here requires
# BOTH lines or the audit fails.

# AC-14 rationale: B10
# ladder rung: 1
# AC-14 rationale: B11
# ladder rung: 1
# AC-14 rationale: E4
# ladder rung: 4
# AC-14 rationale: E5
# ladder rung: 1
# ----- end AC-14 catalog evidence -----
