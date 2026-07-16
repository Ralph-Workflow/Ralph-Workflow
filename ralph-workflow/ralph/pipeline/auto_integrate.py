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
    abort_merge,
    branch_exists,
    branch_sha,
    is_ancestor,
    merge_in_progress,
    merge_target_into_current,
    reset_hard,
    resolve_origin_head_branch,
)
from ralph.git.operations import get_head_sha
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
from ralph.pipeline.auto_integrate_ff import fast_forward_target
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
)
from ralph.pipeline.auto_integrate_record import (
    clear_record as _clear_record,
)
from ralph.pipeline.auto_integrate_record import (
    read_record as _read_record,
)
from ralph.pipeline.auto_integrate_record import (
    write_record as _write_record,
)
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
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
       ONLY if the branch exists in the repository (AC-13).
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
        return _ACTION_SKIPPED, f"rebase failed: {rebase_outcome.kind}"
    # RebaseSuccess or after a clean endpoint merge.
    if merge_attempted and merge_outcome is not None:
        return _ACTION_MERGED, None
    return _ACTION_REBASED, None


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
) -> RebaseState | None:
    """Run the auto-integration step after a successful commit.

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
            config, workspace_scope, state
        )
    except Exception as exc:
        logger.warning("auto_integrate_after_commit: unexpected failure: {}", exc)
        with contextlib.suppress(Exception):
            _clear_record(Path(workspace_scope.root))
        return _record_skip(reason=f"unexpected failure: {exc}", target=None)


def _auto_integrate_after_commit_inner(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    state: RebaseState,
) -> RebaseState | None:
    """Internal worker for :func:`auto_integrate_after_commit`.

    The body is split into three narrow phases so each phase keeps a
    sensible branch / statement count without losing the
    skip-condition table from the product brief:

    1. :func:`_auto_integrate_resolve_context` -- handle the
       ``enabled`` / env-lookup / on-target / no-commits-beyond /
       preconditions skip conditions.
    2. :func:`_run_rebase_or_merge` -- write the crash record, run
       the rebase engine, fall back to the endpoint merge on
       conflict.
    3. Fast-forward + state finalization -- atomic CAS path or
       worktree ff, then build the final ``RebaseState``.
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

    rebase_result = _run_rebase_or_merge(root, target)
    if rebase_result.short_circuit is not None:
        # _run_rebase_or_merge already cleared the record and
        # returned the appropriate conflict / skip RebaseState.
        return rebase_result.short_circuit

    # Success path: the feature branch contains the target.
    feature_sha = _read_post_integration_head_sha(root, target)
    if feature_sha is None:
        return _record_skip(
            reason="post-integration HEAD read failed", target=target
        )

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

    ok, skip_reason = _fast_forward_target(root, target, feature_sha)
    _clear_record(root)

    record = _record_rebase_outcome(
        rebase_outcome=rebase_result.rebase_outcome,
        merge_attempted=rebase_result.merge_attempted,
        merge_outcome=rebase_result.merge_outcome,
        target=target,
    )
    if not ok:
        # Fast-forward skipped: reason is appended but we keep
        # the rebase/merged action as the headline so the log line
        # reflects what actually happened to the feature.
        record = record.model_copy(
            update={
                "last_reason": skip_reason,
                "fast_forwarded": False,
            }
        )
    else:
        # Successful fast-forward: the ``fast_forwarded`` boolean is
        # the headline signal, so any residual reason from the
        # rebase/merge phase (including benign rebase NoOp reasons
        # like ``"Branch is already up-to-date with upstream"``)
        # is scrubbed here. A clean-success state carries no reason.
        record = record.model_copy(
            update={"fast_forwarded": True, "last_reason": None}
        )
    return record


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

    return root, current_branch, target


def _auto_integrate_check_skip_conditions(
    root: Path,
    current_branch: str,
    target: str,
) -> RebaseState | None:
    """Apply the AC-02 / AC-03 / preconditions skip table; return skip or None."""
    if current_branch == target:
        return _record_skip(reason="on target branch", target=target)
    target_sha = branch_sha(root, target)
    if target_sha is not None and not _has_commits_ahead(root, target_sha):
        return _record_skip(reason="no commits beyond target", target=target)
    try:
        check_rebase_preconditions(root)
    except RebasePreconditionError as exc:
        return _record_skip(reason=f"preconditions not met: {exc}", target=target)
    return None


def _has_commits_ahead(root: Path, target_sha: str) -> bool:
    """True when there is at least one commit beyond ``target_sha`` (AC-03)."""
    try:
        return bool(_count_commits_ahead(root, target_sha))
    except Exception:
        return True


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


def _run_rebase_or_merge(root: Path, target: str) -> _RebaseRunResult:
    """Drive rebase_onto, fall back to endpoint merge on conflict.

    On success returns a ``_RebaseRunResult`` with ``short_circuit``
    ``None`` and the ``rebase_outcome`` / ``merge_outcome`` the
    caller uses to build the final :class:`RebaseState`. On a
    rebase failure, a rebase conflict followed by an unresolved
    merge, or a rebase conflict whose abort left the engine in
    progress, returns a ``_RebaseRunResult`` whose ``short_circuit``
    is the appropriate ``RebaseState`` -- the durable crash record
    has already been cleared in that case.
    """
    rebase_outcome = rebase_onto(target, repo_root=root)
    if isinstance(rebase_outcome, RebaseFailed):
        _clear_record(root)
        return _RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=False,
            merge_outcome=None,
            short_circuit=_record_skip(
                reason=f"rebase failed: {rebase_outcome.kind}", target=target
            ),
        )
    if not isinstance(rebase_outcome, RebaseConflicts):
        return _RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=False,
            merge_outcome=None,
            short_circuit=None,
        )

    return _resolve_rebase_conflict(root, target, rebase_outcome)


def _resolve_rebase_conflict(
    root: Path,
    target: str,
    rebase_outcome: RebaseConflicts,
) -> _RebaseRunResult:
    """Abort the conflicted rebase and attempt the endpoint merge (AC-06/AC-07)."""
    _abort_rebase_after_conflict(root)
    if rebase_in_progress(root):
        _clear_record(root)
        return _RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=False,
            merge_outcome=None,
            short_circuit=_record_conflict(
                reason="rebase in-progress after abort", target=target
            ),
        )

    merge_result = _try_endpoint_merge(root, target)
    if merge_result is None:
        # _try_endpoint_merge returned None because the merge
        # attempt raised; surface that as the headline conflict
        # state (the merge-attempt reason is more informative than
        # the generic "both conflicted" message).
        return _RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=True,
            merge_outcome=None,
            short_circuit=_record_conflict(
                reason="rebase conflict followed by merge attempt exception",
                target=target,
            ),
        )
    if merge_result.outcome == "conflict":
        _clear_record(root)
        return _RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=True,
            merge_outcome=merge_result,
            short_circuit=_record_conflict(
                reason="rebase and endpoint merge both conflicted", target=target
            ),
        )

    # RebaseConflicts marker is preserved so _record_rebase_outcome
    # keeps the conflict headline on the resulting RebaseState.
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


def _try_endpoint_merge(root: Path, target: str) -> MergeResult | None:
    """Attempt the endpoint three-way merge; log + clear the record on raise."""
    try:
        return merge_target_into_current(root, target)
    except Exception as merge_exc:
        logger.warning("auto_integrate: merge attempt raised: {}", merge_exc)
        _clear_record(root)
        return None


def _count_commits_ahead(repo_root: Path, target_sha: str) -> int:
    """Return ``git rev-list --count <target_sha>..HEAD`` as int."""
    from ralph.git.subprocess_runner import run_git

    result = run_git(
        ("rev-list", "--count", f"{target_sha}..HEAD"),
        cwd=repo_root,
        label="git-rev-list-count",
    )
    if result.returncode != 0:
        return 1  # conservative
    raw = result.stdout.strip()
    try:
        return int(raw)
    except ValueError:
        return 1


def _continue_fast_forward_from_record(
    workspace_root: Path,
    record: IntegrationRecord,
) -> RebaseState:
    """Best-effort continue of an unfinished fast-forward (phase='integrated').

    Like :func:`recover_incomplete_integration`, this function
    RETAINS the durable record when the fast-forward fails so the
    next startup can retry. The record is cleared only when
    reconciliation succeeded -- the target ref equals
    ``feature_sha`` (already landed) or the worktree-aware CAS path
    moved it to ``feature_sha`` in this call.

    The defensive branches -- malformed record, target advanced
    concurrently, fast-forward refused -- all clear the record
    because they represent permanent states that further retries
    cannot resolve (no SHA to land, target diverged, ff-only
    refused). The transient failure cases -- an exception raised
    inside ``_fast_forward_target`` -- retain the record.
    """
    if record.integrated_feature_sha is None:
        # Defensive: malformed record. Clear it so we stop retrying.
        _clear_record(workspace_root)
        return _record_skip(
            reason="recovery: malformed integrated record", target=record.target
        )
    feature_sha = record.integrated_feature_sha
    if branch_sha(workspace_root, record.target) == feature_sha:
        # Already landed (another run / operator).
        _clear_record(workspace_root)
        return RebaseState(
            last_action=_ACTION_RECOVERED,
            last_reason="already fast-forwarded",
            last_target=record.target,
            fast_forwarded=True,
        )
    if not is_ancestor(workspace_root, record.target, feature_sha):
        # Target advanced and is no longer an ancestor of the feature
        # SHA: this is a permanent state. Clear the record so we
        # don't keep retrying an impossible land.
        _clear_record(workspace_root)
        return _record_skip(
            reason="recovery: target advanced concurrently", target=record.target
        )
    ff_failed = False
    skip_reason = ""
    try:
        ok, skip_reason = _fast_forward_target(
            workspace_root, record.target, feature_sha
        )
    except Exception as exc:
        ff_failed = True
        skip_reason = f"fast-forward raised: {exc}"
        logger.warning("recovery: _fast_forward_target raised: {}", exc)
        ok = False
    if ok:
        _clear_record(workspace_root)
        return RebaseState(
            last_action=_ACTION_RECOVERED,
            last_reason=None,
            last_target=record.target,
            fast_forwarded=True,
        )
    # Fast-forward was refused or raised. Only clear the record for
    # permanent refusals (target advanced concurrently) -- retain it
    # for transient failures so the next startup retries.
    if ff_failed or "advanced concurrently" not in skip_reason:
        # Transient failure: retain the record for retry.
        return RebaseState(
            last_action=_ACTION_SKIPPED,
            last_reason=(
                f"recovery: {skip_reason}; record retained for retry"
            ),
            last_target=record.target,
            fast_forwarded=False,
        )
    # Permanent refusal (target advanced concurrently): clear the
    # record -- a retry won't change the outcome.
    _clear_record(workspace_root)
    return _record_skip(reason=f"recovery: {skip_reason}", target=record.target)


def recover_incomplete_integration(
    workspace_scope: WorkspaceScope,
) -> RebaseState | None:
    """Recover from an interrupted integration at run-loop startup.

    Behavior (AC-11):

    * No durable record → no-op (never disturb an operator's own
      git operation).
    * Abort any owned engine rebase/merge in flight before doing
      anything else. If the abort itself FAILS, the durable record
      is RETAINED so the next startup can retry; the returned
      ``RebaseState`` records the abort failure as a skip with
      the original target preserved.
    * If ``record.phase == 'integrating'``: the rebase/merge never
      completed → restore the feature branch to ``pre_feature_sha``
      via ``reset_hard``. The record is cleared ONLY after
      ``reset_hard`` succeeds AND no owned engine op remains; if
      reset_hard raises or any prior abort left an in-progress
      operation, the record is RETAINED for retry.
    * If ``record.phase == 'integrated'``: the rebase/merge already
      completed and only the fast-forward may be unfinished →
      safely CONTINUE the fast-forward (same worktree-aware CAS path
      used in the happy path).
    * The durable record is cleared only when the recovery path
      confirms (1) no owned git operation remains in flight AND
      (2) the feature SHA was restored to ``pre_feature_sha`` (for
      phase='integrating') OR the fast-forward reconciled cleanly
      (for phase='integrated'). Any failure retains the record so
      the next run can retry the recovery.

    Any unexpected exception inside this function is logged and
    swallowed; it must never abort the run.
    """
    try:
        root = Path(workspace_scope.root)
        record = _read_record(root)
        if record is None:
            return None

        # Step 1: abort any owned engine op we may have left behind.
        # If EITHER abort fails, retain the record so the next
        # startup can retry. This closes the bug where the prior
        # implementation unconditionally called ``_clear_record``
        # after the abort/reset path and returned
        # ``last_action='recovered'`` -- leaving the repository in
        # a rebase/merge state with no ownership marker.
        abort_failed = False
        try:
            if rebase_in_progress(root):
                abort_rebase(repo_root=root)
        except Exception as exc:
            abort_failed = True
            logger.warning("recovery: abort_rebase raised: {}", exc)
        try:
            if merge_in_progress(root):
                abort_merge(root)
        except Exception as exc:
            abort_failed = True
            logger.warning("recovery: abort_merge raised: {}", exc)

        # Step 2: reconcile by phase.
        if record.phase == "integrating":
            # Restore the feature branch to its pre-integration state.
            reset_failed = False
            try:
                reset_hard(root, record.pre_feature_sha)
            except Exception as exc:
                reset_failed = True
                logger.warning("recovery: reset_hard failed: {}", exc)

            # Verify the restore actually landed: read HEAD and
            # compare to pre_feature_sha. If the reset raised OR
            # the verification fails OR an owned op still remains,
            # RETAIN the record so the next startup can retry. Only
            # when all three checks pass do we clear it.
            restored_ok = (
                not abort_failed
                and not reset_failed
                and not rebase_in_progress(root)
                and not merge_in_progress(root)
                and _head_matches_sha(root, record.pre_feature_sha)
            )
            if not restored_ok:
                return RebaseState(
                    last_action=_ACTION_SKIPPED,
                    last_reason=(
                        "recovery: feature branch not restored, record"
                        " retained for retry"
                    ),
                    last_target=record.target,
                    fast_forwarded=False,
                )
            _clear_record(root)
            return RebaseState(
                last_action=_ACTION_RECOVERED,
                last_reason="restored feature branch after interrupted rebase",
                last_target=record.target,
                fast_forwarded=False,
            )

        # phase == 'integrated': continue the fast-forward.
        return _continue_fast_forward_from_record(root, record)
    except Exception as exc:
        logger.warning("recover_incomplete_integration failed: {}", exc)
        return _record_skip(
            reason=f"recovery failed: {exc}", target=None
        )


def _head_matches_sha(repo_root: Path, expected_sha: str) -> bool:
    """Return True when ``HEAD`` resolves to ``expected_sha``.

    Uses ``git rev-parse --verify HEAD`` (a substring match is
    accepted since git treats an unambiguous prefix as a SHA).
    Returns False on any failure so the recovery path retains the
    durable record when verification is impossible -- a defensive
    fail-closed against a half-restored feature branch that we
    cannot prove equals ``pre_feature_sha``.
    """
    try:
        result = run_git(
            ("rev-parse", "--verify", "HEAD"),
            cwd=repo_root,
            label="git-rev-parse-head",
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    return result.stdout.strip() == expected_sha


__all__ = [
    "IntegrationRecord",
    "auto_integrate_after_commit",
    "recover_incomplete_integration",
    "resolve_integration_target",
]
