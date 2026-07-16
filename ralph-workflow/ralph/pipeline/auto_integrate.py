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
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import ConfigDict

from ralph.git.merge import (
    MergeResult,
    abort_merge,
    branch_exists,
    branch_sha,
    compare_and_swap_branch,
    fast_forward_via_worktree,
    is_ancestor,
    merge_in_progress,
    merge_target_into_current,
    reset_hard,
    resolve_origin_head_branch,
    worktree_for_branch,
)
from ralph.git.operations import (
    find_main_worktree_root,
    get_head_sha,
    is_repo_clean,
)
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
from ralph.pipeline.rebase_state import RebaseState
from ralph.pydantic_compat import RalphBaseModel

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.workspace.scope import WorkspaceScope


_AUTO_INTEGRATE_RECORD_FILENAME = "auto_integrate_in_progress.json"

#: Target branch resolution order when ``auto_integrate_target`` is unset.
#: Mirrors the prompt's ``origin/HEAD`` -> ``main`` -> ``master`` cascade.
_AUTO_DETECT_TARGET_CANDIDATES: tuple[str, ...] = ("main", "master")

#: Outcome verbs recorded on ``RebaseState.last_action`` so the runner
#: can format the user-facing log line (rebased / merged /
#: fast-forwarded / skipped / conflict / recovered).
_ACTION_SKIPPED = "skipped"
_ACTION_REBASED = "rebased"
_ACTION_MERGED = "merged"
_ACTION_FAST_FORWARDED = "fast_forwarded"
_ACTION_CONFLICT = "conflict"
_ACTION_RECOVERED = "recovered"


class IntegrationRecord(RalphBaseModel):
    """Durable phased record of an in-progress auto-integration.

    Persisted to ``<workspace_scope.root>/.agent/auto_integrate_in_progress.json``
    via :func:`_write_record` (atomic temp + ``os.replace``) so a
    SIGKILL mid-write leaves the previous record intact and a
    recovery preamble on resume can decide whether to land the
    fast-forward (phase='integrated') or restore the feature branch
    to its pre-integration state (phase='integrating').

    Attributes:
        phase: ``'integrating'`` while the rebase/merge is in flight;
            ``'integrated'`` once the feature branch fully contains
            the target and only the fast-forward remains.
        target: The integration target branch name.
        pre_feature_sha: The feature branch HEAD SHA captured BEFORE
            any rebase/merge; used to restore on a crash that
            interrupts the rebase.
        pre_target_sha: The target branch SHA captured BEFORE the
            fast-forward attempt; the observed ``<oldvalue>`` for
            the atomic compare-and-swap.
        integrated_feature_sha: The feature branch HEAD SHA captured
            AFTER the rebase/merge succeeded. Present only when
            phase='integrated'.
    """

    model_config = ConfigDict(frozen=True)

    phase: str
    target: str
    pre_feature_sha: str
    pre_target_sha: str | None
    integrated_feature_sha: str | None = None


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


def _record_path(workspace_root: Path) -> Path:
    """Return the durable crash-record path anchored to ``workspace_root``."""
    return workspace_root / ".agent" / _AUTO_INTEGRATE_RECORD_FILENAME


def _write_record(workspace_root: Path, record: IntegrationRecord) -> None:
    """Atomically write ``record`` to the durable record path.

    The atomic-write pattern (temp file in the same directory + ``os.replace``)
    mirrors the existing ``ralph.git.operations._atomic_append_text``
    contract: a crash mid-write never leaves a half-written record on
    disk. If ``os.replace`` fails, the staging file is removed before
    re-raising.
    """
    record_file = _record_path(workspace_root)
    record_file.parent.mkdir(parents=True, exist_ok=True)
    payload = record.model_dump_json().encode("utf-8")
    fd, staging_path = tempfile.mkstemp(
        prefix=record_file.name + ".staging.", dir=str(record_file.parent)
    )
    try:
        with os.fdopen(fd, "wb") as staging:
            staging.write(payload)
        Path(staging_path).replace(record_file)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            Path(staging_path).unlink()
        raise


def _read_record(workspace_root: Path) -> IntegrationRecord | None:
    """Return the durable record or ``None`` when absent / corrupt.

    A corrupt record is treated as absent so a partial write from a
    crashed prior run never wedges the recovery preamble.
    """
    record_file = _record_path(workspace_root)
    if not record_file.exists():
        return None
    try:
        raw = record_file.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data_raw: object = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data_raw, dict):
        return None
    try:
        return IntegrationRecord.model_validate(data_raw)
    except Exception:
        return None


def _clear_record(workspace_root: Path) -> None:
    """Unlink the durable record; missing-ok."""
    record_file = _record_path(workspace_root)
    try:
        record_file.unlink()
    except FileNotFoundError:
        return


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
    """Move the local mainline ref to ``feature_sha`` via CAS or worktree ff.

    Returns ``(fast_forwarded, skip_reason)``. ``skip_reason`` is
    empty when the fast-forward succeeded.

    AC-08 atomicity contract: the ancestry decision is BOUND to the
    same observed target SHA the CAS uses. Concretely we observe
    ``observed_target_sha = branch_sha(target)`` ONCE, then verify
    that *that specific SHA* is an ancestor of ``feature_sha``, and
    then CAS that same SHA. If the target moves between observation
    and CAS, the CAS fails closed (the ref no longer equals the
    observed SHA) and we record a concurrency skip. Earlier
    implementations checked ``is_ancestor(target, feature_sha)``
    *before* reading the SHA, leaving a TOCTOU window where a
    concurrent landing between the ancestor check and the SHA read
    could satisfy the CAS with a SHA that was NOT an ancestor of
    ``feature_sha`` -- the bug class closed by this rewrite.

    The worktree path also observes the worktree's branch SHA and
    verifies that observed SHA is an ancestor of ``feature_sha``
    before attempting the worktree ff; ``git merge --ff-only`` is
    itself the second-line guard.

    Never force-moves the target. The skip reasons are recorded in
    the final ``RebaseState.last_reason``.
    """
    # Observe the target SHA FIRST. This is the single value the CAS
    # will use; every downstream check must reference the same SHA.
    observed_target_sha = branch_sha(repo_root, target)
    if observed_target_sha is None:
        return False, "target branch missing at fast-forward time"

    # AC-08 guard: the OBSERVED SHA (not the ref name) must be an
    # ancestor of feature_sha. This is the contract that closes the
    # TOCTOU race: if the target moves after this check, the
    # downstream CAS (or the worktree ff) will refuse the move.
    if not is_ancestor(repo_root, observed_target_sha, feature_sha):
        return False, "target advanced concurrently (not an ancestor of feature)"

    return _fast_forward_target_via_worktree_or_cas(
        repo_root, target, feature_sha, observed_target_sha
    )


def _fast_forward_target_via_worktree_or_cas(
    repo_root: Path,
    target: str,
    feature_sha: str,
    observed_target_sha: str,
) -> tuple[bool, str]:
    """Run the worktree-aware or CAS fast-forward once ancestor + sha checks pass."""
    primary_root = find_main_worktree_root(repo_root)
    wt = worktree_for_branch(primary_root, target)
    if wt is not None:
        return _fast_forward_via_target_worktree(wt, target, feature_sha)
    return _fast_forward_via_cas(repo_root, target, feature_sha, observed_target_sha)


def _fast_forward_via_target_worktree(
    worktree_root: Path,
    target: str,
    feature_sha: str,
) -> tuple[bool, str]:
    """Fast-forward the target branch checked out in ``worktree_root`` (AC-09).

    Re-checks the worktree's currently-checked-out SHA against
    ``feature_sha`` so a concurrent landing inside the worktree
    between the caller-side ``is_ancestor`` and the ``merge --ff-only``
    is still caught: the ancestor guard in the caller references
    ``observed_target_sha`` which is the SHA the caller observed
    via ``branch_sha``; the worktree's own branch SHA is the value
    the worktree's ``HEAD`` resolves to. ``git merge --ff-only`` is
    the second-line atomic guard -- it refuses if ``feature_sha`` is
    not a fast-forward of the worktree's current branch.
    """
    if not is_repo_clean(worktree_root):
        return False, "target worktree dirty"
    if not fast_forward_via_worktree(worktree_root, feature_sha):
        return False, "target advanced concurrently (ff-only refused)"
    return True, ""


def _fast_forward_via_cas(
    repo_root: Path,
    target: str,
    feature_sha: str,
    observed_target_sha: str,
) -> tuple[bool, str]:
    """Atomic CAS fast-forward of a not-checked-out target branch (AC-08)."""
    if not compare_and_swap_branch(repo_root, target, observed_target_sha, feature_sha):
        return False, "target advanced concurrently (CAS mismatch)"
    return True, ""


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
        record = record.model_copy(update={"fast_forwarded": True})
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

    Returns ``None`` when the integration step is a no-op (AC-01
    disabled path) or when the environment lookup itself fails; in
    the latter case :func:`auto_integrate_after_commit` will already
    have logged a skip before reaching this helper.

    The current-branch slot is ``None`` when HEAD is detached
    (GitPython raises ``TypeError`` from ``repo.active_branch.name``
    in that case). The caller surfaces that as a recorded skip
    (``reason='detached HEAD'``) so the operator can see WHY no
    integration ran -- previously a detached HEAD silently fell into
    the broad ``except Exception`` branch and returned ``None``
    with no state recorded.
    """
    try:
        enabled_raw: object = getattr(config.general, "auto_integrate_enabled", True)
        enabled = bool(enabled_raw)
        if not enabled:
            return None
        root = Path(workspace_scope.root)
        # Typed-exception guard: detached HEAD surfaces as
        # ``current_branch is None`` (recorded skip) rather than being
        # absorbed into the broad ``except Exception`` below.
        current_branch: str | None = _current_branch_or_detached_marker(root)
        target: str | None = resolve_integration_target(config, root)
    except Exception as exc:
        logger.debug("auto_integrate_after_commit: skip (env lookup failed): {}", exc)
        return None

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
    """Best-effort continue of an unfinished fast-forward (phase='integrated')."""
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
        _clear_record(workspace_root)
        return _record_skip(
            reason="recovery: target advanced concurrently", target=record.target
        )
    ok, skip_reason = _fast_forward_target(
        workspace_root, record.target, feature_sha
    )
    _clear_record(workspace_root)
    if ok:
        return RebaseState(
            last_action=_ACTION_RECOVERED,
            last_reason=None,
            last_target=record.target,
            fast_forwarded=True,
        )
    return _record_skip(reason=f"recovery: {skip_reason}", target=record.target)


def recover_incomplete_integration(
    workspace_scope: WorkspaceScope,
) -> RebaseState | None:
    """Recover from an interrupted integration at run-loop startup.

    Behavior (AC-11):

    * No durable record → no-op (never disturb an operator's own
      git operation).
    * Abort any owned engine rebase/merge in flight before doing
      anything else.
    * If ``record.phase == 'integrating'``: the rebase/merge never
      completed → restore the feature branch to ``pre_feature_sha``
      via ``reset_hard``.
    * If ``record.phase == 'integrated'``: the rebase/merge already
      completed and only the fast-forward may be unfinished →
      safely CONTINUE the fast-forward (same worktree-aware CAS path
      used in the happy path).
    * Always clear the record before returning so the next commit
      phase starts clean.

    Any unexpected exception inside this function is logged and
    swallowed; it must never abort the run.
    """
    try:
        root = Path(workspace_scope.root)
        record = _read_record(root)
        if record is None:
            return None

        # Step 1: abort any owned engine op we may have left behind.
        try:
            if rebase_in_progress(root):
                abort_rebase(repo_root=root)
        except Exception as exc:
            logger.warning("recovery: abort_rebase raised: {}", exc)
        try:
            if merge_in_progress(root):
                abort_merge(root)
        except Exception as exc:
            logger.warning("recovery: abort_merge raised: {}", exc)

        # Step 2: reconcile by phase.
        if record.phase == "integrating":
            # Restore the feature branch to its pre-integration state.
            try:
                reset_hard(root, record.pre_feature_sha)
            except Exception as exc:
                logger.warning("recovery: reset_hard failed: {}", exc)
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


__all__ = [
    "IntegrationRecord",
    "auto_integrate_after_commit",
    "recover_incomplete_integration",
    "resolve_integration_target",
]
