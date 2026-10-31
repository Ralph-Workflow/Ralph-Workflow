"""Detached-HEAD reattach fallback helpers (GAP-1 / AC-10).

The exact-tip reattach path in :mod:`ralph.pipeline.commit_executor`
requires the branch tip to equal the detached HEAD's SHA. The
fast-forward and fresh-branch fallbacks in this module
unblock the reattach when a Ralph-induced reset or snapshot has
left the branch tip behind the detached commit.

Two-step fallback (in :func:`reattach_head_if_detached`):

1. **Ancestry-verified fast-forward** -- scan the candidate list,
   exclude the integration target and any branch currently
   checked out in another worktree, run
   ``git merge-base --is-ancestor <branch-tip> <detached-head>``,
   then compare-and-swap ``git update-ref <ref> <new-oid>
   <old-oid>`` (the observed branch tip is the ``<old-oid>``).

2. **Fresh branch with collision-safe naming** -- when no
   candidate is safe, create a fresh branch at the detached SHA
   with a collision-safe name (``<preferred>-<7-char-sha>`` on
   collision; never overwrites an existing branch).

The detached commit is preserved on the chosen branch and the
worktree's ``HEAD`` is reattached through the same
``reattach_head_to_branch`` helper the exact-tip path uses, so
``create_commit`` succeeds on the next seam and the work lands
on the configured integration target through the normal
auto-integrate flow.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.subprocess_runner import run_git
from ralph.pipeline._auto_integrate_recovery_git_state import (
    reattach_head_to_branch,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


#: A detached commit is a 40-char SHA; the first 7 hex chars are
#: a stable short identifier for collision-safe branch naming.
_DETACHED_HEAD_SHORT_SHA_LEN: int = 7


def integration_target_branch(**opts: object) -> str | None:
    """Return the configured integration target branch, if any.

    The integration target is the *destination* of a successful
    integration, not the *source* branch the work lives on;
    fast-forwarding it would clobber remote commits. The
    candidate is therefore excluded from the fast-forward
    fallback (GAP-1 / AC-10 / C1). The candidate generator
    already yields it, so the exclusion lives here in the
    resolver to keep the generator source-agnostic.
    """
    for key in ("integration_target", "auto_integrate_target", "target_branch"):
        val = opts.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    config = opts.get("config")
    if config is not None:
        for attr in ("auto_integrate_target", "integration_target", "target_branch"):
            attr_val: object = getattr(config, attr, None)
            if isinstance(attr_val, str) and attr_val.strip():
                return attr_val.strip()
        general_obj: object = getattr(config, "general", None)
        if general_obj is not None:
            for gen_attr in ("auto_integrate_target", "integration_target", "target_branch"):
                target_val: object = getattr(general_obj, gen_attr, None)
                if isinstance(target_val, str) and target_val.strip():
                    return target_val.strip()
    return None


class WorktreeOccupancy(frozenset[str]):
    """Branches checked out in other worktrees, with a fail-closed query verdict.

    A successful ``git worktree list`` yields the plain set of occupied
    branches. When the query fails (git error, non-zero exit) Ralph cannot
    know which branches a sibling worktree holds, so the verdict is
    ``query_failed`` and membership answers ``True`` for EVERY branch: each
    caller that skips occupied candidates (exact-tip reattach, fast-forward,
    fresh-branch naming) therefore fails closed without a separate check,
    mirroring :func:`ralph.git.merge.worktree_lookup`'s
    ``WORKTREE_QUERY_FAILED`` verdict.
    """

    __slots__ = ("_query_failed",)

    _query_failed: bool

    def __new__(
        cls, branches: frozenset[str] = frozenset(), *, query_failed: bool = False
    ) -> WorktreeOccupancy:
        """Build the occupancy verdict from the parsed ``branches``."""
        instance = super().__new__(cls, branches)
        instance._query_failed = query_failed
        return instance

    @property
    def query_failed(self) -> bool:
        """Return whether the worktree query failed (occupancy unknown)."""
        return self._query_failed

    def __contains__(self, branch: object) -> bool:
        """Treat every branch as occupied when the query failed."""
        return self._query_failed or super().__contains__(branch)


def occupied_worktree_branches(repo_root: Path, **opts: object) -> WorktreeOccupancy:
    """Return branches currently checked out in OTHER worktrees.

    A fast-forward that strands another agent's working copy is
    forbidden (GAP-1 / AC-10 / C1). The current worktree is
    excluded by definition -- its checked-out branch is the
    detached HEAD we are trying to reattach.

    When ``git worktree list`` cannot be read the result carries
    ``query_failed`` and reports every branch as occupied (fail
    closed) instead of an empty set that would let a caller move a
    branch a sibling worktree has checked out.
    """
    occupied: set[str] = set()
    raw_occupied = opts.get("occupied_worktree_branches") or opts.get("occupied")
    if isinstance(raw_occupied, (set, frozenset, list, tuple)):
        occupied.update(str(name) for name in raw_occupied)
    try:
        res = run_git(
            ("worktree", "list", "--porcelain"),
            cwd=repo_root,
            label="commit:worktree-list-occupied",
        )
    except Exception as exc:
        logger.warning(
            "reattach fallback: worktree list failed ({}); treating every branch as occupied", exc
        )
        return WorktreeOccupancy(frozenset(occupied), query_failed=True)
    if res.returncode != 0:
        logger.warning(
            "reattach fallback: worktree list exited {}; treating every branch as occupied",
            res.returncode,
        )
        return WorktreeOccupancy(frozenset(occupied), query_failed=True)
    try:
        resolved_root = repo_root.resolve()
    except Exception:
        resolved_root = repo_root
    current_path: str | None = None
    for line in res.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = line[len("worktree ") :].strip()
        elif line.startswith("branch refs/heads/") and current_path:
            try:
                if Path(current_path).resolve() == resolved_root:
                    continue
            except Exception:
                pass
            occupied.add(line[len("branch refs/heads/") :].strip())
    return WorktreeOccupancy(frozenset(occupied))


def branch_tip_sha(repo_root: Path, branch: str) -> str | None:
    """Return the SHA of ``refs/heads/<branch>``, or ``None`` if unreadable."""
    try:
        result = run_git(
            ("rev-parse", "--verify", f"refs/heads/{branch}"),
            cwd=repo_root,
            label="commit:branch-tip-sha-read",
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def fast_forward_reattach(
    repo_root: Path,
    *,
    detached_head_sha: str | None,
    candidates: Iterator[str],
    branch_exists: Callable[[Path, str], bool],
    integration_target: str | None,
    occupied: frozenset[str],
) -> bool:
    """Fast-forward a candidate branch to the detached HEAD via CAS.

    The candidate list is the same priority order as the exact-tip
    resolver (reflog, worktree metadata, config, repository name);
    the integration target and any branch currently checked out in
    another worktree are excluded. The ancestry check
    (``git merge-base --is-ancestor <branch-tip> <detached-head>``)
    is the structural pre-condition for a fast-forward. The
    compare-and-swap ``git update-ref <ref> <new-oid> <old-oid>``
    refuses to clobber a concurrent ref change: a non-zero exit
    means the candidate is unsafe, and the resolver falls through
    to the next candidate (or the fresh-branch path).
    """
    from ralph.git.merge import is_ancestor as _is_ancestor

    if detached_head_sha is None:
        return False
    if isinstance(occupied, WorktreeOccupancy) and occupied.query_failed:
        logger.warning(
            "reattach fallback: worktree occupancy unknown; skipping fast-forward so no "
            "branch checked out in another worktree is moved"
        )
        return False
    for candidate in candidates:
        if not branch_exists(repo_root, candidate):
            continue
        if integration_target and candidate == integration_target:
            logger.debug(
                "reattach fallback: skipping integration target '{}' (clobber risk)",
                candidate,
            )
            continue
        if candidate in occupied:
            logger.debug(
                "reattach fallback: skipping occupied worktree branch '{}'",
                candidate,
            )
            continue
        branch_tip = branch_tip_sha(repo_root, candidate)
        if branch_tip is None or branch_tip == detached_head_sha:
            # Equal-tip case is handled by step 1; skip here to
            # avoid a no-op CAS.
            continue
        if not _is_ancestor(repo_root, branch_tip, detached_head_sha):
            logger.debug(
                "reattach fallback: candidate '{}' tip {} is not an ancestor of detached HEAD {}",
                candidate,
                branch_tip,
                detached_head_sha,
            )
            continue
        # CAS: refuse to overwrite the ref if a concurrent change
        # mutated it between the read and the update.
        try:
            update = run_git(
                (
                    "update-ref",
                    f"refs/heads/{candidate}",
                    detached_head_sha,
                    branch_tip,
                ),
                cwd=repo_root,
                label="commit:fast-forward-cas",
            )
        except Exception:
            update = None
        if update is None or update.returncode != 0:
            logger.debug(
                "reattach fallback: CAS refused for '{}' (rc={})",
                candidate,
                None if update is None else update.returncode,
            )
            continue
        logger.info(
            "Detached HEAD fast-forwarded to branch '{}' ({} -> {})",
            candidate,
            branch_tip[:7] if branch_tip else "?",
            detached_head_sha[:7] if detached_head_sha else "?",
        )
        reattach_head_to_branch(repo_root, candidate)
        return True
    return False


def create_fresh_branch_for_detached(
    repo_root: Path,
    *,
    detached_head_sha: str | None,
    candidates: Iterator[str],
    branch_exists: Callable[[Path, str], bool],
    integration_target: str | None,
    occupied: frozenset[str],
) -> str | None:
    """Create a fresh branch at the detached HEAD with collision-safe naming.

    The preferred name is the first candidate that does not match
    an existing branch. On collision (a branch with the preferred
    name already exists at a divergent SHA), the name is suffixed
    with ``-<7-char-sha>`` of the detached HEAD; on second
    collision, the resolver falls back to ``wt-<7-char-sha>``. The
    integration target is NEVER a preferred name (clobber risk);
    the resolver already excludes it in
    :func:`integration_target_branch`.
    """
    if detached_head_sha is None:
        return None
    short_sha = detached_head_sha[:_DETACHED_HEAD_SHORT_SHA_LEN]
    preferred: str | None = None
    for candidate in candidates:
        if integration_target and candidate == integration_target:
            continue
        if candidate in occupied:
            continue
        if not preferred:
            preferred = candidate
            break
    if not preferred:
        chosen = f"wt-{short_sha}"
    else:
        chosen = preferred
        if branch_exists(repo_root, preferred):
            collision = f"{preferred}-{short_sha}"
            chosen = collision if not branch_exists(repo_root, collision) else f"wt-{short_sha}"
    if branch_exists(repo_root, chosen):
        suffix_idx = 1
        while branch_exists(repo_root, f"{chosen}-{suffix_idx}"):
            suffix_idx += 1
        chosen = f"{chosen}-{suffix_idx}"
    try:
        create_branch = run_git(
            ("branch", chosen, detached_head_sha),
            cwd=repo_root,
            label="commit:create-fresh-branch",
        )
    except Exception:
        return None
    if create_branch.returncode != 0:
        return None
    return chosen
