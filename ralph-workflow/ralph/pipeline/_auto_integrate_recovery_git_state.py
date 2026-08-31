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


def lock_holder_is_dead(lock_path: Path) -> bool:
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
    wrapper is re-exported by
    :mod:`ralph.pipeline.auto_integrate_recovery` under its former
    ``recovery._lock_holder_is_dead`` name, which is the seam
    :mod:`ralph.pipeline.auto_integrate_catalog_rationales`
    and the test suite reference.
    """
    from ralph.pipeline.auto_integrate_recovery_lock import (
        _lock_holder_is_dead as _impl,
    )

    return _impl(lock_path)


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
