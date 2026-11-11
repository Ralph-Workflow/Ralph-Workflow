
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.subprocess_runner import run_git
from ralph.pipeline._auto_integrate_recovery_git_state import (
    common_git_dir,
    reattach_head_to_branch,
    rebase_bookkeeping_dir,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


_DETACHED_HEAD_SHORT_SHA_LEN: int = 7


def integration_target_branch(**opts: object) -> str | None:
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

    __slots__ = ("_query_failed",)

    _query_failed: bool

    def __new__(
        cls, branches: frozenset[str] = frozenset(), *, query_failed: bool = False
    ) -> WorktreeOccupancy:
        instance = super().__new__(cls, branches)
        instance._query_failed = query_failed
        return instance

    @property
    def query_failed(self) -> bool:
        return self._query_failed

    def __contains__(self, branch: object) -> bool:
        return self._query_failed or super().__contains__(branch)


def occupied_worktree_branches(repo_root: Path, **opts: object) -> WorktreeOccupancy:
    occupied: set[str] = set()
    raw_occupied = opts.get("occupied_worktree_branches") or opts.get("occupied")
    if isinstance(raw_occupied, (set, frozenset, list, tuple)):
        occupied.update(str(name) for name in raw_occupied)
    try:
        common = common_git_dir(repo_root) or rebase_bookkeeping_dir(repo_root)
        if (
            common is not None
            and not (common / "worktrees").is_dir()
            and (repo_root / ".git").is_dir()
        ):
            return WorktreeOccupancy(frozenset(occupied))
    except OSError:
        pass
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


_HEX_LEN: int = 40


def _direct_branch_tip_sha(repo_root: Path, branch: str) -> str | None:
    clean = branch.strip() if branch else ""
    if not clean:
        return None
    try:
        common = common_git_dir(repo_root) or rebase_bookkeeping_dir(repo_root)
        if common is not None:
            ref_file = common / "refs" / "heads" / clean
            if ref_file.is_file():
                sha = ref_file.read_text(encoding="utf-8", errors="replace").strip()
                if len(sha) == _HEX_LEN and all(c in "0123456789abcdefABCDEF" for c in sha):
                    return sha
    except OSError:
        pass
    return None


def branch_tip_sha(repo_root: Path, branch: str) -> str | None:
    direct = _direct_branch_tip_sha(repo_root, branch)
    if direct is not None:
        return direct
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
            continue
        if not _is_ancestor(repo_root, branch_tip, detached_head_sha):
            logger.debug(
                "reattach fallback: candidate '{}' tip {} is not an ancestor of detached HEAD {}",
                candidate,
                branch_tip,
                detached_head_sha,
            )
            continue
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
