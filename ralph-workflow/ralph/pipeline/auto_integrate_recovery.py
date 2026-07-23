"""Startup crash recovery for interrupted auto-integrations (AC-11).

Extracted from :mod:`ralph.pipeline.auto_integrate` to keep that module
under the repo-structure ``_MAX_FILE_LINES`` cap. The public entry
point :func:`recover_incomplete_integration` is re-exported by
``ralph.pipeline.auto_integrate`` so existing imports and monkeypatch
targets keep working unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import (
    MERGE_STATE_NONE,
    abort_merge,
    branch_sha,
    is_ancestor,
    merge_state,
    reset_hard,
)
from ralph.git.operations import is_repo_clean
from ralph.git.rebase.rebase import abort_rebase, rebase_in_progress
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate_context import (
    record_refresh,
    refresh_outcome_is_healthy,
)
from ralph.pipeline.auto_integrate_ff import (
    fast_forward_target,
    maybe_push_target,
)
from ralph.pipeline.auto_integrate_record import (
    clear_record as _clear_record,
)
from ralph.pipeline.auto_integrate_record import (
    read_record as _read_record,
)
from ralph.pipeline.auto_integrate_refresh import refresh_target as _refresh_target
from ralph.pipeline.auto_integrate_sync import REFRESH_UNREACHABLE
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.workspace.scope import WorkspaceScope

#: Mirrors of the outcome verbs in :mod:`ralph.pipeline.auto_integrate`
#: (kept local so this module never imports its re-exporting parent).
_ACTION_SKIPPED = "skipped"
_ACTION_RECOVERED = "recovered"


class TerminalStateViolationError(RuntimeError):
    """Raised when the R6/AC-06 terminal-state invariant is violated.

    The pipeline contract: an integration attempt that exits MUST leave
    the git dir in a state where ``check_rebase_preconditions`` would
    pass (no in-progress markers unless a live resolution session owns
    them), AND on the abort path HEAD must resolve to the recorded
    pre-attempt SHA. When either condition fails, this exception is
    raised after the diagnostic is logged so the caller surfaces the
    leak loudly and the recovery code path retains the durable record
    / backup ref until the invariant is restored.
    """

#: Terminal-state invariant marker files. The post-attempt
#: verification refuses to clear the durable record while ANY of
#: these is on disk AND no resolution session owns them. The
#: closed set is small on purpose: a missing file is the loud
#: signal that the rebase/merge was not terminated cleanly, and
#: an unknown file (the spec's "A8 benign leftovers") is
#: deliberately NOT a marker -- git's own bookkeeping
#: (``AUTO_MERGE``, ``MERGE_MSG``, ``MERGE_MODE``, ``MERGE_RR``,
#: ``SQUASH_MSG``, ``ORIG_HEAD``) is allowed to survive because
#: treating it as a blocker permanently disabled integration in
#: the past.
_TERMINAL_MARKER_FILES: frozenset[str] = frozenset(
    {
        "rebase-merge",
        "rebase-apply",
        "REBASE_HEAD",
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "sequencer",
    }
)


def recovery_retained_record(state: RebaseState | None) -> bool:
    """Whether ``state`` came back still OWNING the durable record.

    :func:`recover_incomplete_integration` has two shapes of outcome.
    Either it reconciled the interrupted operation -- cleared the
    durable :class:`~ralph.pipeline.auto_integrate_record.IntegrationRecord`
    after proving the feature branch was restored or the fast-forward
    landed -- or it hit a transient failure and deliberately LEFT the
    record on disk so the next startup can retry it.

    In the second shape recovery is not finished, and the caller must
    NOT start a fresh integration in the same startup:
    ``auto_integrate._integrate_once`` writes a new
    ``IntegrationRecord(phase='integrating', ...)`` before it touches
    git, which overwrites the only durable metadata describing the
    still-unreconciled operation -- the pre-integration feature SHA a
    later recovery needs to restore, and the target the interrupted
    landing was aimed at.

    Callers read the retention fact THROUGH this predicate rather than
    from ``last_reason``: that string is operator display text, is
    formatted differently on every branch, and interpolates exception
    messages. ``None`` (no record existed, recovery was a no-op) is not
    retained.
    """
    return state is not None and state.recovery_record_retained


def post_attempt_verify(
    root: Path,
    *,
    expected_head_sha: str | None,
    owns_resolution: bool,
) -> None:
    """Terminal-state invariant check used on EVERY exit path (R6).

    RAISES :class:`TerminalStateViolationError` on failure so the caller
    surfaces a loud diagnostic AND the recovery code path retains
    the durable record / backup ref until the invariant is restored
    (the analysis feedback's AC-06 contract: a failed invariant
    must be loud, not silent-and-ignored).

    A failure is detected when:

    * any in-progress marker from :data:`_TERMINAL_MARKER_FILES`
      remains in the per-worktree git dir, AND ``owns_resolution``
      is False (a live resolution session is editing the conflict
      and will clean up its own state); OR
    * on the abort path (``expected_head_sha`` is not ``None``),
      ``HEAD`` resolves to something other than that SHA -- never
      ``ORIG_HEAD``, which any intervening operation can overwrite.

    The function is intentionally side-effect-free: it READS
    state, never WRITES it. Recovery / abort paths that run AFTER
    this call decide what to do with a failed invariant (the
    standard contract is: do not delete the backup ref or clear
    the durable record until the invariant passes).
    """
    git_dir = _rebase_bookkeeping_dir(root)
    if git_dir is None:
        # Not a git checkout: nothing to verify; pass.
        return
    if not owns_resolution:
        for marker in _TERMINAL_MARKER_FILES:
            if (git_dir / marker).exists():
                raise TerminalStateViolationError(
                    f"terminal-state invariant violated: {marker} "
                    f"present in {git_dir}"
                )
    if expected_head_sha is not None and not _head_matches_sha(root, expected_head_sha):
        actual = _read_head_sha(root)
        raise TerminalStateViolationError(
            "terminal-state invariant violated: HEAD is "
            f"{actual or '<unreadable>'}, expected {expected_head_sha}"
        )


def _read_head_sha(root: Path) -> str | None:
    """Read HEAD as a string SHA; ``None`` on any failure.

    Distinct from :func:`_head_matches_sha` because the verifier
    needs the VALUE for the operator log, not just a boolean
    match.
    """
    try:
        result = run_git(
            ("rev-parse", "--verify", "HEAD"),
            cwd=root,
            label="git-rev-parse-head-verify",
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _rebase_bookkeeping_dir(root: Path) -> Path | None:
    """Resolve the git dir whose rebase bookkeeping blocks preconditions.

    ``git rev-parse --git-dir`` returns the PRIVATE per-worktree dir for
    a linked worktree -- the same dir
    :func:`ralph.git.rebase.rebase_preconditions.check_rebase_preconditions`
    reads its blocking markers from. ``None`` when git cannot be asked;
    the caller treats that as "nothing observable to reclaim".
    """
    try:
        result = run_git(("rev-parse", "--git-dir"), cwd=root, label="recovery-git-dir")
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    git_dir = Path(result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (root / git_dir).resolve()
    return git_dir


def _reclaim_unowned_stale_rebase(root: Path) -> RebaseState | None:
    """Reclaim inert rebase state that no ownership record accounts for.

    Failure mode this closes (recorded on wt-23, 2026-07-22):
    a leftover ``rebase-merge``/``rebase-apply`` directory
    or lone ``REBASE_HEAD`` marker with NO durable record fails
    ``check_rebase_preconditions`` at every seam -- startup, phase
    boundaries, the commit seam -- forever, and nothing ever cleans it,
    because recovery's no-record branch used to return ``None``
    unconditionally. That is the forbidden permanent silent noop.

    The discriminator against AC-11 case 4 (an operator's live
    in-progress rebase, which must stay byte-unchanged) is worktree
    cleanliness: a live conflict resolution has unmerged paths and
    tracked modifications, while inert stale state sits on a clean
    tree -- the only shape the boundary hook can meet, since it fires
    exclusively on clean trees.

    Returns ``None`` when there is nothing to reclaim or the state is
    protected (dirty tree); a ``recovered`` state after a successful
    reclaim; a ``skipped`` state when the reclaim itself failed.

    Implementation note: the actual reclaim work is delegated to a
    sequence of small helpers (``_recover_rebase_state``,
    ``_recover_merge_marker``, ``_recover_sequencer_state``,
    ``_recover_detached_head``) so this orchestrator stays below the
    ruff PLR0912 (too-many-branches) cap while each branch lives in
    a focused helper that can be unit-tested in isolation.
    """
    git_dir = _rebase_bookkeeping_dir(root)
    if git_dir is None:
        # Not a git checkout (or git unaskable): nothing observable to
        # reclaim, and the pre-fix contract for the no-record branch --
        # return ``None``, never a synthesized outcome -- must hold.
        return None
    blocking = _collect_blocking_markers(git_dir, root)
    if blocking is None:
        # ``None`` here means "live contention, do not reclaim".
        return None
    if not blocking:
        return None
    if not is_repo_clean(root):
        # AC-11 case 4: unmerged paths / tracked modifications mean an
        # operator (or live agent) owns this rebase. Preserve it.
        logger.warning(
            "recovery: unowned in-progress rebase found in {} but the tree"
            " is dirty; preserving it (operator-owned, AC-11 case 4)",
            root,
        )
        return None
    logger.warning(
        "recovery: reclaiming stale unowned rebase/merge state in {} (no"
        " ownership record, clean worktree) -- it was permanently blocking"
        " auto-integration preconditions",
        root,
    )
    try:
        _recover_rebase_state(root, git_dir)
        _recover_merge_marker(root, git_dir)
        _recover_sequencer_state(root, git_dir)
        _recover_detached_head(root, git_dir)
        # A9: the live-PID guard already cleared the index.lock for
        # us; nothing more to do.
    except Exception as exc:
        logger.warning("recovery: failed to reclaim stale unowned rebase state: {}", exc)
        return _record_skip(
            reason=f"recovery: stale unowned rebase state could not be reclaimed: {exc}",
            target=None,
        )
    return RebaseState(
        last_action=_ACTION_RECOVERED,
        last_reason=(
            "reclaimed stale unowned rebase/merge state (no ownership record, clean worktree)"
        ),
        last_target=None,
        fast_forwarded=False,
    )


def _collect_blocking_markers(git_dir: Path, root: Path) -> list[str] | None:
    """Collect the blocking markers ``git_dir`` carries.

    Returns ``None`` when a LIVE ``index.lock`` holder means the
    reclaim must defer (E9 contention, NOT staleness). Returns an
    empty list when there is nothing to reclaim at all. Otherwise
    returns the list of marker names (``"index.lock"`` included when
    the holder is provably dead) that justify a reclaim.
    """
    blocking: list[str] = [name for name in _TERMINAL_MARKER_FILES if (git_dir / name).exists()]
    index_lock = git_dir / "index.lock"
    if not index_lock.exists():
        return blocking
    if _lock_holder_is_dead(index_lock):
        blocking.append("index.lock")
        return blocking
    logger.warning(
        "recovery: live index.lock holder PID in {}; leaving it",
        git_dir,
    )
    return None


def _recover_rebase_state(root: Path, git_dir: Path) -> None:
    """Clear rebase bookkeeping under ``git_dir``.

    A3: a corrupted state dir cannot be aborted with git's own
    commands because git refuses --abort/--continue when
    ``head-name``/``onto``/``todo`` are missing. We trust the
    local ``refs/heads/<branch>`` rather than git's state
    files, so the recover strategy is "delete the state dir,
    then leave the ref alone". A lone ``REBASE_HEAD`` marker
    (no state dir) is removed directly -- ``git rebase --abort``
    is not available for it but the precondition check blocks
    on its presence.
    """
    rebase_state_dir = _select_rebase_state_dir(git_dir)
    if rebase_state_dir is not None:
        if _rebase_state_dir_is_corrupt(rebase_state_dir):
            _remove_path(rebase_state_dir)
        else:
            abort_rebase(repo_root=root)
    if (git_dir / "REBASE_HEAD").exists() and not rebase_in_progress(root):
        _remove_path(git_dir / "REBASE_HEAD")


def _select_rebase_state_dir(git_dir: Path) -> Path | None:
    """Return the active rebase state dir under ``git_dir`` if any.

    Prefers ``rebase-merge`` (the merge backend's directory) and
    falls back to ``rebase-apply`` (the apply backend). Returns
    ``None`` when neither exists so callers skip the per-dir
    cleanup.
    """
    if (git_dir / "rebase-merge").exists():
        return git_dir / "rebase-merge"
    if (git_dir / "rebase-apply").exists():
        return git_dir / "rebase-apply"
    return None


def _recover_merge_marker(root: Path, git_dir: Path) -> None:
    """Clear a stale ``MERGE_HEAD`` after attempting ``git merge --abort``.

    ``git merge --abort`` can refuse a synthetic / truncated
    ``MERGE_HEAD``; on a clean tree the marker itself is the only
    thing blocking preconditions, so we remove it directly when
    ``--abort`` could not.
    """
    if not (git_dir / "MERGE_HEAD").exists():
        return
    if merge_state(root) == MERGE_STATE_NONE:
        return
    aborted = abort_merge(root)
    if aborted:
        return
    if (git_dir / "MERGE_HEAD").exists():
        _remove_path(git_dir / "MERGE_HEAD")


def _recover_sequencer_state(root: Path, git_dir: Path) -> None:
    """Clear cherry-pick / revert residue under ``git_dir``.

    A6: sequencer operations leave ``CHERRY_PICK_HEAD`` /
    ``REVERT_HEAD`` / ``.git/sequencer`` on disk. ``sequencer/todo``
    exists iff a sequencer op is paused. Each marker is removed
    individually so partial residue does not leak across recovery
    attempts.
    """
    sequencer_todo = git_dir / "sequencer" / "todo"
    if not (
        (git_dir / "CHERRY_PICK_HEAD").exists()
        or (git_dir / "REVERT_HEAD").exists()
        or sequencer_todo.exists()
    ):
        return
    if (git_dir / "CHERRY_PICK_HEAD").exists():
        _run_sequencer_quit(root, "cherry-pick")
    if (git_dir / "REVERT_HEAD").exists():
        _run_sequencer_quit(root, "revert")
    for marker in ("CHERRY_PICK_HEAD", "REVERT_HEAD"):
        if (git_dir / marker).exists():
            _remove_path(git_dir / marker)
    sequencer_dir = git_dir / "sequencer"
    if sequencer_dir.exists():
        _remove_path(sequencer_dir)


def _recover_detached_head(root: Path, git_dir: Path) -> None:
    """Re-attach a detached HEAD residue to its original branch.

    A11: detached-HEAD residue (no state dir, HEAD on a raw
    OID) is re-attached to the original branch. The local ref
    still holds the branch tip; the HEAD is just detached, so
    updating HEAD back to ``refs/heads/<branch>`` is enough.
    """
    if not _detached_head_no_state(root, git_dir):
        return
    branch = _detached_branch_name(root)
    if branch is None:
        return
    _reattach_head_to_branch(root, branch)


def _remove_path(path: Path) -> None:
    """Remove a file or directory, recursively; missing-ok; never raises."""
    if not path.exists():
        return
    if path.is_dir():
        import shutil

        try:
            shutil.rmtree(path)
        except Exception as exc:  # pragma: no cover -- defensive
            logger.warning("recovery: could not rmtree {}: {}", path, exc)
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            return


def _lock_holder_is_dead(lock_path: Path) -> bool:
    """True when the index.lock holder PID is provably dead (A9).

    A live holder is contention (E9), not staleness -- the lock
    MUST be left in place so the concurrent writer finishes, and
    the bounded retry loop backs off. A PID that is missing,
    unreadable, or that the OS reports as ``NoSuchProcess`` is
    treated as dead; any other error (a sandbox that hides
    ``/proc`` etc.) is treated as LIVE so a missed reclaim costs
    one backoff rather than a corrupt checkout.

    The PID is read via the standard git convention: a single
    line of plain text (the writing process's PID) in the lock
    file itself. Older gits wrote nothing here, so an empty /
    whitespace-only file is treated as "no PID", which the
    spec resolves as dead (A9: "liveness check, not age").

    Implementation lives in
    :mod:`ralph.pipeline.auto_integrate_recovery_lock`; this
    wrapper preserves the ``recovery._lock_holder_is_dead``
    seam referenced from
    :mod:`ralph.pipeline.auto_integrate_catalog_rationales`
    and the test suite.
    """
    from ralph.pipeline.auto_integrate_recovery_lock import (
        _lock_holder_is_dead as _impl,
    )

    return _impl(lock_path)


def _rebase_state_dir_is_corrupt(state_dir: Path) -> bool:
    """True when the rebase state dir is missing ``head-name``/``onto``.

    A corrupt state dir cannot be ``--abort``'d or ``--continue``'d
    (A3). Git's own recovery would fall over; our recover strategy
    removes the dir entirely and trusts the local ref.
    """
    if not state_dir.is_dir():
        return False
    return not (state_dir / "head-name").exists() and not (state_dir / "onto").exists()


def _run_sequencer_quit(root: Path, op: str) -> None:
    """Run ``git <op> --quit`` defensively. Never raises."""
    try:
        run_git(
            (op, "--quit"),
            cwd=root,
            label=f"recovery:sequencer-quit:{op}",
        )
    except Exception as exc:
        logger.warning("recovery: sequencer --quit for '{}' failed: {}", op, exc)


def _detached_head_no_state(root: Path, git_dir: Path) -> bool:
    """True when HEAD is detached AND no rebase/sequencer state dir exists."""
    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        return False
    if (git_dir / "sequencer").exists():
        return False
    try:
        result = run_git(
            ("symbolic-ref", "--quiet", "HEAD"),
            cwd=root,
            label="recovery:head-symbolic-ref",
        )
    except Exception:
        return False
    return result.returncode != 0


def _detached_branch_name(root: Path) -> str | None:
    """Return the original branch name from the reflog when detached.

    A detached-HEAD residue often still has the original branch name
    in HEAD (e.g. ``ref: refs/heads/feature\\0<sha>``) -- but
    ``symbolic-ref`` already returned non-zero above, so we fall
    through to the reflog: the last entry that points at
    ``refs/heads/<name>`` is the branch the agent was on before
    the residue.
    """
    try:
        result = run_git(
            ("reflog", "--format=%gD", "-n", "1"),
            cwd=root,
            label="recovery:head-reflog",
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    line = result.stdout.strip()
    parts_after_to_count = 2
    for marker in ("checkout: moving from ", "switch branch to "):
        idx = line.find(marker)
        if idx == -1:
            continue
        rest = line[idx + len(marker) :]
        parts = rest.split(" to ")
        if len(parts) == parts_after_to_count:
            return parts[1].strip() or None
    return None


def _reattach_head_to_branch(root: Path, branch: str) -> None:
    """``git symbolic-ref HEAD refs/heads/<branch>`` to repair detached residue."""
    try:
        run_git(
            ("symbolic-ref", "HEAD", f"refs/heads/{branch}"),
            cwd=root,
            label="recovery:head-reattach",
        )
    except Exception as exc:
        logger.warning("recovery: could not re-attach HEAD to '{}': {}", branch, exc)


def _record_skip(*, reason: str, target: str | None, record_retained: bool = False) -> RebaseState:
    """Build a ``RebaseState`` recording a recovery skip outcome.

    ``record_retained`` marks the skips that left the durable record on
    disk for the next startup to retry. It defaults to ``False`` so a
    skip only claims ownership when its branch explicitly says so; see
    :func:`recovery_retained_record`.
    """
    return RebaseState(
        last_action=_ACTION_SKIPPED,
        last_reason=reason,
        last_target=target,
        fast_forwarded=False,
        recovery_record_retained=record_retained,
    )


def _refresh_before_verdict(
    config: UnifiedConfig | None,
    workspace_root: Path,
    target: str,
) -> tuple[str | None, bool]:
    """Re-read the mainline pointer the recovery verdicts are taken from.

    Both verdicts below -- "already landed" and "target advanced
    concurrently" -- are decided from ``branch_sha``, and the second one
    CLEARS the durable record permanently. Taking that decision from a
    pointer this process has never fetched is how a perfectly landable
    integration gets discarded after a crash.

    Returns ``(outcome, pointer_is_fresh)``:

    * ``outcome`` is the ``REFRESH_*`` verb the caller stamps on the
      returned state, or ``None`` when no config was supplied.
    * ``pointer_is_fresh`` says whether the target pointer the verdicts
      are about to read can be trusted. It is ``True`` when no config
      was supplied -- that caller opted out of the seam entirely and
      keeps the pre-seam behaviour byte-for-byte -- and otherwise only
      when the refresh actually returned a HEALTHY outcome
      (:func:`~ralph.pipeline.auto_integrate_context.refresh_outcome_is_healthy`).
      A refresh that RAISED, or that came back unreachable / diverged /
      race-lost, establishes no fresh verdict, so the caller must fail
      closed rather than clear the durable record from a pointer
      nothing vouched for.

    A raise is reported as
    :data:`~ralph.pipeline.auto_integrate_sync.REFRESH_UNREACHABLE`
    rather than ``None`` so the skip the caller returns names why the
    landing was deferred instead of looking like no refresh was even
    attempted. Never raises: recovery's contract is that it swallows
    everything.
    """
    if config is None:
        return None, True
    try:
        outcome = _refresh_target(config, workspace_root, target)
    except Exception as exc:
        logger.warning("recovery: target refresh raised: {}", exc)
        return REFRESH_UNREACHABLE, False
    return outcome, refresh_outcome_is_healthy(outcome)


def _continue_fast_forward_from_record(
    workspace_root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None = None,
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
    inside ``fast_forward_target`` -- retain the record.

    Those "permanent" verdicts are only permanent when the pointer
    they read is genuinely current, so when a refresh was configured
    and could not establish one this function FAILS CLOSED: it
    returns a retryable skip and RETAINS the record without
    evaluating either ancestry verdict. A refresh that raised or came
    back unreachable used to degrade to ``None`` here, after which a
    stale local pointer could still take the "target advanced
    concurrently" branch and discard a perfectly landable
    integration for good.
    """
    if record.integrated_feature_sha is None:
        # Defensive: malformed record. Clear it so we stop retrying.
        _clear_record(workspace_root)
        return _record_skip(reason="recovery: malformed integrated record", target=record.target)
    feature_sha = record.integrated_feature_sha
    # ONE refresh, before either verdict: both of them are taken from the
    # target pointer, and the second one clears the record for good.
    refresh, pointer_is_fresh = _refresh_before_verdict(config, workspace_root, record.target)
    if not pointer_is_fresh:
        # Fail closed. Neither ancestry verdict below may be taken from
        # a pointer nothing vouched for, because both of them clear the
        # durable record and one of them does so permanently. Retain it
        # and let the next startup retry against a pointer that can be
        # refreshed.
        return record_refresh(
            _record_skip(
                reason=(
                    "recovery: target pointer could not be refreshed, record retained for retry"
                ),
                target=record.target,
                record_retained=True,
            ),
            refresh,
        )
    if branch_sha(workspace_root, record.target) == feature_sha:
        # Already landed (another run / operator). Even here we
        # route through the push hook: a sibling that landed
        # locally does not necessarily push to every remote (its
        # config may differ or it may have been crashed mid-push),
        # and the AC-03 contract says the auto-integrate subsystem
        # is the place every local advance reaches every remote
        # when push is enabled.
        _clear_record(workspace_root)
        return record_refresh(
            maybe_push_target(
                config,
                workspace_root,
                record.target,
                RebaseState(
                    last_action=_ACTION_RECOVERED,
                    last_reason="already fast-forwarded",
                    last_target=record.target,
                    fast_forwarded=True,
                ),
            ),
            refresh,
        )
    if not is_ancestor(workspace_root, record.target, feature_sha):
        # Target advanced and is no longer an ancestor of the feature
        # SHA: this is a permanent state. Clear the record so we
        # don't keep retrying an impossible land.
        _clear_record(workspace_root)
        return record_refresh(
            _record_skip(
                reason="recovery: target advanced concurrently",
                target=record.target,
            ),
            refresh,
        )
    return _land_and_reconcile(workspace_root, record, feature_sha, refresh, config)


def _land_and_reconcile(
    workspace_root: Path,
    record: IntegrationRecord,
    feature_sha: str,
    refresh: str | None,
    config: UnifiedConfig | None = None,
) -> RebaseState:
    """Run the fast-forward and decide whether the record survives it.

    Split out of :func:`_continue_fast_forward_from_record` so each
    function keeps one job: the caller decides whether the pointer may
    be trusted at all, and this one interprets what the landing did.

    Only a PERMANENT refusal ("advanced concurrently") clears the
    record. A refusal that raised, or any other refusal text, is
    treated as transient and RETAINS the record so the next startup
    retries.

    The successful-landing branch routes through
    :func:`ralph.pipeline.auto_integrate_ff.maybe_push_target` so the
    AC-03 every-successful-advance-pushes contract is honoured at the
    recovery landing site too: a crashed previous run that retried its
    fast-forward on the next startup must push to every configured
    remote just as the happy path does, with ``config=None`` (the
    pre-seam behaviour) bypassing the push byte-for-byte.
    """
    ff_failed = False
    skip_reason = ""
    try:
        ok, skip_reason = fast_forward_target(workspace_root, record.target, feature_sha)
    except Exception as exc:
        ff_failed = True
        skip_reason = f"fast-forward raised: {exc}"
        logger.warning("recovery: fast_forward_target raised: {}", exc)
        ok = False
    # R6/AC-06: post-attempt terminal-state verification on EVERY
    # exit path of the recovery fast-forward. The landing either
    # succeeded (no expected-head-sha check needed -- the target
    # moved to feature_sha, not the feature) or it recorded a loud
    # retryable skip (the record is retained in that branch below).
    # We always clean up any ``refs/rebase-backup/<id>`` the
    # attempt may have left behind.
    _delete_rebase_backup_refs(workspace_root)
    if ok:
        _clear_record(workspace_root)
        return record_refresh(
            maybe_push_target(
                config,
                workspace_root,
                record.target,
                RebaseState(
                    last_action=_ACTION_RECOVERED,
                    last_reason=None,
                    last_target=record.target,
                    fast_forwarded=True,
                ),
            ),
            refresh,
        )
    if ff_failed or "advanced concurrently" not in skip_reason:
        # Transient failure: retain the record for retry.
        return record_refresh(
            _record_skip(
                reason=f"recovery: {skip_reason}; record retained for retry",
                target=record.target,
                record_retained=True,
            ),
            refresh,
        )
    # Permanent refusal (target advanced concurrently): clear the
    # record -- a retry won't change the outcome.
    _clear_record(workspace_root)
    return record_refresh(
        _record_skip(reason=f"recovery: {skip_reason}", target=record.target),
        refresh,
    )


def recover_incomplete_integration(
    workspace_scope: WorkspaceScope,
    *,
    config: UnifiedConfig | None = None,
) -> RebaseState | None:
    """Recover from an interrupted integration at run-loop startup.

    Behavior (AC-11):

    * No durable record → no-op (never disturb an operator's own
      git operation).
    * Abort any owned engine rebase/merge in flight before doing
      anything else. If the abort itself FAILS, the durable record
      is RETAINED so the next startup can retry; the returned
      ``RebaseState`` records the abort failure as a skip with
      the original target preserved. The merge check is read via
      :func:`ralph.git.merge.merge_state`, so a FAILED git query
      counts as a possible in-flight merge (abort attempted, record
      retained unless a readable state later proves ``MERGE_HEAD``
      absent) rather than as "nothing to abort".
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
    * Every outcome that RETAINS the record sets
      ``RebaseState.recovery_record_retained``, which callers read
      through :func:`recovery_retained_record`. A caller that sees it
      must not start a fresh integration in the same startup: the
      integration writes its own durable record before mutating git
      and would overwrite the unreconciled one.

    Args:
        workspace_scope: Scope whose root holds the durable record.
        config: Run configuration, used ONLY to re-read the mainline
            pointer before the phase='integrated' ancestry verdicts.
            Optional, and ``None`` reproduces the pre-seam behaviour
            exactly, so a caller that holds no config keeps working.

    Any unexpected exception inside this function is logged and
    swallowed; it must never abort the run.
    """
    try:
        root = Path(workspace_scope.root)
        record = _read_record(root)
        if record is None:
            # No durable record: either nothing happened, or stale
            # unowned rebase bookkeeping is silently blocking every
            # integration seam. Reclaim the latter (clean tree only;
            # a dirty tree is operator-owned and preserved).
            return _reclaim_unowned_stale_rebase(root)

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
                if record.resolving_rebase:
                    # Named distinctly so an operator can tell this apart
                    # from an ordinary crashed rebase. The rebase is still
                    # ABORTED, never resumed: the agent session that was
                    # editing it died with the previous process, and a new
                    # process resuming a half-resolved replay it never saw
                    # would land whatever the dead agent happened to have
                    # written when it was killed.
                    logger.warning(
                        "recovery: found a rebase onto '{}' interrupted while a "
                        "conflict-resolution agent was working; aborting it "
                        "(an orphaned resolution is never resumed)",
                        record.target,
                    )
                abort_rebase(repo_root=root)
        except Exception as exc:
            abort_failed = True
            logger.warning("recovery: abort_rebase raised: {}", exc)
        try:
            # MERGE_STATE_UNKNOWN deliberately lands in this branch:
            # "git could not be asked" is not evidence that no merge
            # is in flight, so the abort is attempted and only a
            # POSITIVE post-abort MERGE_STATE_NONE counts as success.
            if merge_state(root) != MERGE_STATE_NONE:
                aborted = abort_merge(root)
                if not aborted and merge_state(root) != MERGE_STATE_NONE:
                    abort_failed = True
                    logger.warning(
                        "recovery: merge abort did not prove MERGE_HEAD gone in {}",
                        root,
                    )
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
                and merge_state(root) == MERGE_STATE_NONE
                and _head_matches_sha(root, record.pre_feature_sha)
            )
            if not restored_ok:
                return _record_skip(
                    reason=("recovery: feature branch not restored, record retained for retry"),
                    target=record.target,
                    record_retained=True,
                )
            _clear_record(root)
            return RebaseState(
                last_action=_ACTION_RECOVERED,
                last_reason="restored feature branch after interrupted rebase",
                last_target=record.target,
                fast_forwarded=False,
            )

        # phase == 'integrated': continue the fast-forward. A failed
        # (or unprovable) abort retains the record here too -- clearing
        # it while an owned merge may still be in flight would strand
        # the repository with no ownership marker.
        if abort_failed:
            return _record_skip(
                reason=("recovery: owned merge not proven aborted, record retained for retry"),
                target=record.target,
                record_retained=True,
            )
        return _continue_fast_forward_from_record(root, record, config)
    except Exception as exc:
        # Deliberately NOT marked ``record_retained``. This is the
        # catch-all for an unexpected failure anywhere above, including
        # before the record was even read, so it cannot honestly claim
        # ownership of a record it may never have seen. Claiming it
        # would gate the startup catch-up behind a fault that has no
        # bounded end, which is the opposite of recovery's fail-open
        # contract. The named retention branches above are the ones that
        # know a record is on disk.
        logger.warning("recover_incomplete_integration failed: {}", exc)
        return _record_skip(reason=f"recovery failed: {exc}", target=None)


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


def _delete_rebase_backup_refs(root: Path) -> None:
    """Delete every ``refs/rebase-backup/<id>`` ref under ``root``.

    B11/E5 cleanup: when a verified land or abort completes, the
    backup refs created in :func:`auto_integrate._create_rebase_backup_ref`
    must be deleted so they do not accumulate. ``update-ref -d``
    on a non-existent ref returns 1 and is treated as a no-op, so
    the function is safe to call at every recovery / integrate exit
    path.

    Uses ``git for-each-ref`` to enumerate -- ``refs/heads``-style
    reads would miss the ``refs/rebase-backup/`` namespace. The
    function never raises: a backup ref left around is recoverable
    on the next run (it does not block integration), while a stuck
    attempt on a missing backup would.
    """
    try:
        result = run_git(
            ("for-each-ref", "--format=%(refname)", "refs/rebase-backup/"),
            cwd=root,
            label="recovery:list-backup-refs",
        )
    except Exception as exc:  # pragma: no cover -- defensive
        logger.warning(
            "recovery: backup-ref enumeration raised unexpectedly: {}; "
            "stale backups will be discovered by the next attempt",
            exc,
        )
        return
    if result.returncode != 0:
        return
    for raw_ref in result.stdout.splitlines():
        ref = raw_ref.strip()
        if not ref:
            continue
        try:
            delete_result = run_git(
                ("update-ref", "-d", ref),
                cwd=root,
                label=f"recovery:delete-backup-ref:{ref}",
            )
        except Exception as exc:  # pragma: no cover -- defensive
            logger.warning(
                "recovery: backup-ref deletion raised unexpectedly for {}: {}",
                ref,
                exc,
            )
            continue
        if delete_result.returncode not in (0, 1):
            stderr = (delete_result.stderr or "").strip()
            logger.warning(
                "recovery: backup-ref deletion failed for {} (rc={}): {}",
                ref,
                delete_result.returncode,
                stderr[:200],
            )


# ----- AC-14 catalog evidence -----
# This file is the authoritative source for the catalog entries listed
# below. Each ``# AC-14 rationale: <ID>`` line is the code-adjacent
# marker the AC-14 audit looks for; each ``# ladder rung: <N>``
# names the rung the entry sits on. Adding a new entry here requires
# BOTH lines or the audit fails.

# AC-14 rationale: A1
# ladder rung: 2
# AC-14 rationale: A10
# ladder rung: 2
# AC-14 rationale: A11
# ladder rung: 2
# AC-14 rationale: A3
# ladder rung: 2
# AC-14 rationale: A4
# ladder rung: 1
# AC-14 rationale: A5
# ladder rung: 2
# AC-14 rationale: A6
# ladder rung: 2
# AC-14 rationale: A9
# ladder rung: 2
# AC-14 rationale: C5
# ladder rung: 2
# AC-14 rationale: E10
# ladder rung: 4
# AC-14 rationale: E7
# ladder rung: 1
# AC-14 rationale: E9
# ladder rung: 3
# AC-14 rationale: F6
# ladder rung: 3
# AC-14 rationale: G3
# ladder rung: 1
# AC-14 rationale: G4
# ladder rung: 1
# AC-14 rationale: H7
# ladder rung: 4
# ----- end AC-14 catalog evidence -----
