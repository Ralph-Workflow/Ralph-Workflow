"""Read and repair the on-disk git state a dead process left in a worktree.

Every function here is a primitive over ONE piece of git's operation
bookkeeping: the per-worktree git dir a paused rebase's markers live in,
the ref locks a killed writer left behind, the rebase state directory
and whether it is intact enough for git's own ``--abort`` to work, the
sequencer's ``--quit``, and a detached HEAD's way back to its branch.

They are gathered here because they share one property that makes them
awkward to read beside the recovery policy: none of them decides
anything. Each answers a question about the filesystem, or performs one
bounded repair, and NEVER raises -- an unreadable or unrepairable piece
of state has to degrade into "nothing to reclaim" rather than abort the
run. :mod:`ralph.pipeline.auto_integrate_recovery` keeps the policy that
decides whether a reclaim may happen at all, and re-exports these under
their former private names so the seams the catalog rationales and the
test suite reference still resolve.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from ralph.git.subprocess_runner import run_git

__all__ = [
    "common_dir_lock_paths",
    "detached_branch_name",
    "detached_head_no_state",
    "lock_holder_is_dead",
    "reattach_head_to_branch",
    "rebase_bookkeeping_dir",
    "rebase_state_dir_is_corrupt",
    "remove_path",
    "run_sequencer_quit",
    "select_rebase_state_dir",
]


def rebase_bookkeeping_dir(root: Path) -> Path | None:
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


def common_git_dir(root: Path) -> Path | None:
    """Return the shared git dir (``git rev-parse --git-common-dir``), or None."""
    try:
        result = run_git(("rev-parse", "--git-common-dir"), cwd=root, label="git-common-dir")
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    common = Path(result.stdout.strip())
    return common if common.is_absolute() else (root / common).resolve()


def common_dir_lock_paths(root: Path) -> tuple[Path, ...]:
    """Return shared ref-lock paths without guessing linked-worktree layout."""
    try:
        result = run_git(
            ("rev-parse", "--git-common-dir"), cwd=root, label="recovery-git-common-dir"
        )
    except OSError:
        return ()
    if result.returncode != 0 or not result.stdout.strip():
        return ()
    common_dir = Path(result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = (root / common_dir).resolve()
    ref_dir = common_dir / "refs"
    try:
        ref_locks = tuple(ref_dir.rglob("*.lock")) if ref_dir.is_dir() else ()
    except OSError:
        ref_locks = ()
    return (common_dir / "HEAD.lock", common_dir / "packed-refs.lock", *ref_locks)


def select_rebase_state_dir(git_dir: Path) -> Path | None:
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


def remove_path(path: Path) -> None:
    """Remove a file or directory, recursively; missing-ok; never raises."""
    if not path.exists():
        return
    if path.is_dir():
        import shutil

        try:
            # filesystem-write-ok: bounded cleanup of abandoned auto-integration worktree state
            shutil.rmtree(path)
        except Exception as exc:  # pragma: no cover -- defensive
            logger.warning("recovery: could not rmtree {}: {}", path, exc)
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            return


def lock_holder_is_dead(
    lock_path: Path | str,
    gitdir: Path | str | None = None,
    gitdir_common: Path | str | None = None,
) -> bool:
    """True when the lock has no live holder and is old enough to be stale.

    A live holder is contention, not staleness -- the lock MUST be left
    in place so the concurrent writer finishes, and the bounded retry
    loop backs off. Requires both the age threshold to be met and a
    conclusive 'dead' result from the liveness probe.

    Implementation lives in
    :mod:`ralph.pipeline.auto_integrate_recovery_lock`; this
    wrapper is re-exported by
    :mod:`ralph.pipeline.auto_integrate_recovery` under its former
    ``recovery._lock_holder_is_dead`` name, which is the seam
    :mod:`ralph.pipeline.auto_integrate_catalog_rationales`
    and the test suite reference.
    """
    from ralph.pipeline.auto_integrate_recovery_lock import (
        _lock_holder_is_dead as _impl,
    )

    return _impl(lock_path, gitdir=gitdir, gitdir_common=gitdir_common)


def rebase_state_dir_is_corrupt(state_dir: Path) -> bool:
    """True when the rebase state dir is missing ``head-name``/``onto``.

    A corrupt state dir cannot be ``--abort``'d or ``--continue``'d
    (A3). Git's own recovery would fall over; our recover strategy
    removes the dir entirely and trusts the local ref.
    """
    if not state_dir.is_dir():
        return False
    return not (state_dir / "head-name").exists() and not (state_dir / "onto").exists()


def run_sequencer_quit(root: Path, op: str) -> None:
    """Run ``git <op> --quit`` defensively. Never raises."""
    try:
        run_git(
            (op, "--quit"),
            cwd=root,
            label=f"recovery:sequencer-quit:{op}",
        )
    except Exception as exc:
        logger.warning("recovery: sequencer --quit for '{}' failed: {}", op, exc)


def detached_head_no_state(root: Path, git_dir: Path) -> bool:
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


def _resolve_reflog_candidate(root: Path, candidate: str | None) -> str | None:
    """Verify a reflog-derived branch candidate against the exact-tip invariant.

    Returns the candidate when:
    - the candidate is a non-empty string;
    - ``refs/heads/<candidate>`` exists (show-ref exits 0);
    - the branch tip rev-parses to a non-empty OID;
    - ``git rev-parse HEAD`` succeeds AND produces a non-empty OID AND the
      candidate's tip EQUALS detached HEAD exactly (no ``is_ancestor``
      relaxation, no fall-through to older reflog entries, no empty-string
      equal-empty-string fall-through).

    Returns ``None`` on any failed observation.  A failed show-ref means the
    destination was a SHA (explicit detached checkout) and is fail-closed;
    a failed tip/head rev-parse, an empty rev-parse stdout, a missing
    candidate, or a non-equal tip is also fail-closed.  An empty
    rev-parse stdout is treated as UNKNOWN (not as a successful zero-OID
    observation) so a successful-empty rev-parse cannot authorize a
    reattachment via the ``"" == ""`` equality path.
    """
    if not candidate:
        return None
    branch_check = run_git(
        ("show-ref", "--verify", f"refs/heads/{candidate}"),
        cwd=root,
        label="recovery:branch-verify",
    )
    if branch_check.returncode != 0:
        return None
    tip_check = run_git(
        ("rev-parse", "--verify", f"refs/heads/{candidate}"),
        cwd=root,
        label="recovery:branch-tip",
    )
    # Empty rev-parse stdout is UNKNOWN: refuse to use it as proof that
    # two observations match (the ``"" == ""`` trap that previously
    # authorized reattachment on two missing observations).
    if tip_check.returncode != 0 or not tip_check.stdout.strip():
        return None
    candidate_tip = tip_check.stdout.strip()
    head_check = run_git(
        ("rev-parse", "--verify", "HEAD"),
        cwd=root,
        label="recovery:head-rev-parse",
    )
    if head_check.returncode != 0 or not head_check.stdout.strip():
        return None
    detached_head_oid = head_check.stdout.strip()
    if candidate_tip != detached_head_oid:
        return None
    return candidate


def _is_checkout_or_switch_subject(subject: str) -> bool:
    """True if a reflog subject is a checkout or switch action.

    This is the first-pass classifier used by :func:`detached_branch_name`.
    The subject is the right-hand side of a ``"<sha>\\x00<subject>"`` reflog
    line.  A line that is not a checkout or switch (commit, merge, cherry-
    pick, reset, etc.) returns ``False`` and the caller continues scanning
    older entries.
    """
    return ("checkout: moving from " in subject) or ("switch branch to " in subject)


def _parse_checkout_destination(subject: str) -> str | None:
    """Return the destination of a checkout/switch subject, or ``None``.

    A well-formed ``checkout: moving from <src> to <dest>`` (or
    ``switch branch to <dest>``) subject returns ``<dest>`` stripped of
    surrounding whitespace.  Returns ``None`` for any of:

    * a subject that is not a checkout/switch (caller should have filtered
      with :func:`_is_checkout_or_switch_subject` first);
    * a checkout/switch subject without a `` to `` separator (malformed
      half-line, e.g. ``checkout: moving from main`` with no destination);
    * a subject whose destination is empty or whitespace-only (e.g.
      ``checkout: moving from main to ``).

    The two failure modes (irrelevant entry vs. malformed entry) are
    intentionally distinguished: the caller's recovery contract is to
    STOP scanning on the first checkout/switch entry regardless of
    validity, so a malformed newest entry must return ``None`` and
    trigger fail-closed.
    """
    for marker in ("checkout: moving from ", "switch branch to "):
        idx = subject.find(marker)
        if idx == -1:
            continue
        rest = subject[idx + len(marker) :]
        parts = rest.split(" to ", 1)
        # Two halves: "<source> to <destination>"; 1-half means malformed
        source_dest: tuple[str, str] = ("", "")  # typing hint to avoid PLR2004
        if len(parts) != len(source_dest):
            return None
        return parts[1].strip() or None
    return None


def detached_branch_name(root: Path) -> str | None:
    """Return the original branch name from the reflog when detached.

    A detached-HEAD residue often still has the original branch name
    in HEAD (e.g. ``ref: refs/heads/feature\\0<sha>``) -- but
    ``symbolic-ref`` already returned non-zero above, so we fall
    through to the reflog: the last entry that points at
    ``refs/heads/<name>`` is the branch the agent was on before
    the residue.
    """
    try:
        # Distinct reflog contract #2 (worktree-private reflog): this reads
        # the worktree-private <git-dir>/logs/HEAD -- NOT the commondir; in a
        # worktree `git rev-parse --git-path logs/HEAD` resolves to
        # .git/worktrees/<name>/logs/HEAD. It reconstructs the original branch
        # name from detached-HEAD residue, and cwd=root pins it to the
        # operation's own worktree. This is a crash-recovery seam, NOT
        # diagnostic evidence; it is a DISTINCT contract from the commondir
        # branch reflog read in _integration_continuation (see comment there).
        #
        # Format: %H (commit hash) + null + %gs (reflog subject).  Each entry
        # is "<sha>\x00<subject>", entries separated by newlines.  %gD (reflog
        # selector, e.g. "HEAD@{0}") contains NO subject text -- that is why
        # %gs (subject) is required here.
        result = run_git(
            ("reflog", "--format=%H%x00%gs", "-n", "50"),
            cwd=root,
            label="recovery:head-reflog",
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None

    # Parse each entry: "<sha>\x00<subject>".  The reflog is ordered
    # most-recent-first.  We scan until we hit the first checkout/switch entry
    # -- that entry is the one that put us in the detached state, so it is the
    # only authoritative observation: if its "to" field is a SHA, the user
    # explicitly left the branch context and there is no branch to recover to;
    # if it is a branch name, the candidate must be exact-equivalent to
    # detached HEAD -- the symbolic-ref-only reattachment at
    # :func:`reattach_head_to_branch` preserves the work only when the branch
    # tip already points at detached HEAD (any other branch tip would silently
    # lose the detached commits).  Fail-closed: a single failed observation
    # returns ``None`` and never falls through to an older reflog entry.
    #
    # The two-step classifier (is_checkout_or_switch_subject followed by
    # parse_checkout_destination) intentionally separates "irrelevant entry"
    # (commit/merge/cherry-pick -- continue scanning) from "malformed
    # checkout entry" (no " to " separator or empty destination -- fail
    # closed and DO NOT scan older entries).  An earlier implementation
    # conflated these cases by returning None for both, which let a
    # malformed newest entry be skipped in favor of an older valid one.
    for line in result.stdout.splitlines():
        if "\x00" not in line:
            continue
        _sha, subject = line.split("\x00", 1)
        # Skip non-checkout lines (commits, merges, cherry-picks).
        if not _is_checkout_or_switch_subject(subject):
            continue

        # This is the first checkout/switch entry we encountered.  The
        # entry's destination is either a valid non-empty string, a SHA
        # (which ``_resolve_reflog_candidate`` rejects via show-ref), an
        # empty string, or a malformed half-line with no " to " separator.
        # Each of those outcomes is fail-closed: the resolver returns
        # ``None`` for SHA/empty/malformed and never falls through to
        # older reflog entries.  This is the invariant-10 ownership
        # boundary -- UNKNOWN observations must not authorize a branch
        # selection.
        candidate = _parse_checkout_destination(subject)
        return _resolve_reflog_candidate(root, candidate)
    return None


def reattach_head_to_branch(root: Path, branch: str) -> None:
    """``git symbolic-ref HEAD refs/heads/<branch>`` to repair detached residue."""
    try:
        run_git(
            ("symbolic-ref", "HEAD", f"refs/heads/{branch}"),
            cwd=root,
            label="recovery:head-reattach",
        )
    except Exception as exc:
        logger.warning("recovery: could not re-attach HEAD to '{}': {}", branch, exc)
