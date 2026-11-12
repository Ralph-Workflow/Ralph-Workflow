from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import suppress
from pathlib import Path
from typing import Final

from loguru import logger

from ralph.git.subprocess_runner import run_git
from ralph.pipeline._auto_integrate_recovery_git_state import (
    common_git_dir,
    detached_branch_name,
    detached_head_no_state,
    reattach_head_to_branch,
    rebase_bookkeeping_dir,
)

_HEX_SHA_LEN: Final[int] = 40
_REF_PARTS_COUNT: Final[int] = 2

type _EnvGetter = Callable[[str], str | None]
_DEFAULT_ENV_GETTER: _EnvGetter = os.environ.get


def _is_git_worktree(repo_root: Path) -> bool:
    git_dir = rebase_bookkeeping_dir(repo_root)
    if git_dir is not None and git_dir.is_dir():
        return True
    try:
        res = run_git(
            ("rev-parse", "--is-inside-work-tree"), cwd=repo_root, label="check-git-worktree"
        )
        return res.returncode == 0 and res.stdout.strip() == "true"
    except Exception:
        return False


def _read_detached_head_from_git_dir(git_dir: Path) -> bool | None:
    head_file = git_dir / "HEAD"
    if not head_file.is_file():
        return None
    try:
        line = head_file.read_text(encoding="utf-8", errors="replace").strip()
        if line.startswith("ref:"):
            return False
        if len(line) >= _HEX_SHA_LEN:
            has_state = (
                (git_dir / "rebase-merge").exists()
                or (git_dir / "rebase-apply").exists()
                or (git_dir / "sequencer").exists()
            )
            return not has_state
    except OSError:
        pass
    return None


def _is_head_detached(repo_root: Path) -> bool:
    if not _is_git_worktree(repo_root):
        return False
    git_dir = rebase_bookkeeping_dir(repo_root)
    if git_dir is not None:
        fs_check = _read_detached_head_from_git_dir(git_dir)
        if fs_check is not None:
            return fs_check
        if detached_head_no_state(repo_root, git_dir):
            return True
    try:
        cmd = ("symbolic-ref", "--quiet", "HEAD")
        return run_git(cmd, cwd=repo_root, label="commit:head-symbolic-ref").returncode != 0
    except Exception:
        return False


_head_is_detached = _is_head_detached


def _branch_exists(repo_root: Path, branch: str) -> bool:
    clean = branch.strip() if branch else ""
    if not clean:
        return False
    common = common_git_dir(repo_root) or rebase_bookkeeping_dir(repo_root)
    if common is not None:
        if (common / "refs" / "heads" / clean).is_file():
            return True
        packed = common / "packed-refs"
        if packed.is_file():
            try:
                if f"refs/heads/{clean}\n" in packed.read_text(encoding="utf-8", errors="replace"):
                    return True
            except OSError:
                pass
    try:
        cmd = ("show-ref", "--verify", f"refs/heads/{clean}")
        return run_git(cmd, cwd=repo_root, label="check-branch").returncode == 0
    except Exception:
        return False


def _direct_detached_branch_from_reflog(git_dir: Path, repo_root: Path) -> str | None:
    reflog_path = git_dir / "logs" / "HEAD"
    if not reflog_path.is_file():
        return None
    with suppress(OSError):
        lines = reflog_path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in reversed(lines[-50:]):
            parts = line.split("\t", 1)
            if len(parts) == _REF_PARTS_COUNT:
                subject = parts[1].strip()
                for marker in ("checkout: moving from ", "switch branch to "):
                    idx = subject.find(marker)
                    if idx == -1:
                        continue
                    rest = subject[idx + len(marker) :]
                    src, *dest_parts = rest.split(" to ", 1)
                    src = src.strip()
                    if src and _branch_exists(repo_root, src):
                        return src
                    if dest_parts:
                        dest = dest_parts[0].strip()
                        if dest and _branch_exists(repo_root, dest):
                            return dest
    return None


def _detached_branch_from_reflog(repo_root: Path) -> str | None:
    git_dir = rebase_bookkeeping_dir(repo_root)
    if git_dir is not None:
        direct = _direct_detached_branch_from_reflog(git_dir, repo_root)
        if direct is not None:
            return direct
    try:
        result = run_git(
            ("reflog", "--format=%H%x00%gs", "-n", "50"),
            cwd=repo_root,
            label="commit:reflog-source-branch",
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if "\x00" not in line:
            continue
        _sha, subject = line.split("\x00", 1)
        for marker in ("checkout: moving from ", "switch branch to "):
            idx = subject.find(marker)
            if idx == -1:
                continue
            rest = subject[idx + len(marker) :]
            src, *dest_parts = rest.split(" to ", 1)
            src = src.strip()
            if src and _branch_exists(repo_root, src):
                return src
            if dest_parts:
                dest = dest_parts[0].strip()
                if dest and _branch_exists(repo_root, dest):
                    return dest
    return None


def _branch_from_worktree_metadata(repo_root: Path) -> str | None:
    git_dir = rebase_bookkeeping_dir(repo_root)
    if git_dir is not None:
        head_path = git_dir / "HEAD"
        if head_path.is_file():
            try:
                content = head_path.read_text(encoding="utf-8", errors="replace").strip()
                if content.startswith("ref: refs/heads/"):
                    b = content[len("ref: refs/heads/") :].strip()
                    if _branch_exists(repo_root, b):
                        return b
            except OSError:
                pass
    try:
        res = run_git(
            ("worktree", "list", "--porcelain"),
            cwd=repo_root,
            label="commit:worktree-list",
        )
    except Exception:
        return None
    if res.returncode != 0:
        return None
    current_matches = False
    try:
        resolved_root = repo_root.resolve()
    except Exception:
        resolved_root = repo_root
    for line in res.stdout.splitlines():
        if line.startswith("worktree "):
            path_str = line[len("worktree ") :].strip()
            try:
                current_matches = Path(path_str).resolve() == resolved_root
            except Exception:
                current_matches = False
        elif current_matches and line.startswith("branch refs/heads/"):
            b = line[len("branch refs/heads/") :].strip()
            if _branch_exists(repo_root, b):
                return b
    return None


def _candidate_branch_names(repo_root: Path, **opts: object) -> Iterator[str]:
    branch = detached_branch_name(repo_root)
    if branch:
        yield branch

    from_reflog = _detached_branch_from_reflog(repo_root)
    if from_reflog:
        yield from_reflog

    from_wt = _branch_from_worktree_metadata(repo_root)
    if from_wt:
        yield from_wt

    for key in ("branch", "feature_branch", "target_branch"):
        val = opts.get(key)
        if isinstance(val, str) and val.strip():
            yield val.strip()

    raw_env_getter = opts.get("env_getter")
    env_getter: _EnvGetter = raw_env_getter if callable(raw_env_getter) else _DEFAULT_ENV_GETTER
    env_branch = env_getter("FEATURE_BRANCH")
    if env_branch and env_branch.strip():
        yield env_branch.strip()

    config = opts.get("config")
    if config is not None:
        for attr in ("feature_branch", "branch", "target_branch"):
            attr_val: object = getattr(config, attr, None)
            if isinstance(attr_val, str) and attr_val.strip():
                yield attr_val.strip()
        general_obj: object = getattr(config, "general", None)
        if general_obj is not None:
            for general_attr in ("feature_branch", "branch"):
                gen_val: object = getattr(general_obj, general_attr, None)
                if isinstance(gen_val, str) and gen_val.strip():
                    yield gen_val.strip()

    yield repo_root.name


def _resolve_branch_for_reattach(repo_root: Path, **opts: object) -> str | None:
    from ralph.pipeline._commit_reattach_fallback import (
        integration_target_branch,
        occupied_worktree_branches,
    )

    integration_target = integration_target_branch(**opts)
    occupied = occupied_worktree_branches(repo_root, **opts)

    for candidate in _candidate_branch_names(repo_root, **opts):
        if not candidate:
            continue
        if candidate == repo_root.name:
            continue
        if integration_target and candidate == integration_target:
            logger.debug(
                "Skipping reattach candidate '{}': matches integration target",
                candidate,
            )
            continue
        if candidate in occupied:
            logger.debug(
                "Skipping reattach candidate '{}': occupied by worktree",
                candidate,
            )
            continue
        detached_head_sha = _detached_head_sha(repo_root)
        if not _branch_tip_matches(repo_root, candidate, detached_head_sha):
            logger.debug(
                "Skipping reattach candidate '{}': branch tip does not match detached HEAD ({})",
                candidate,
                detached_head_sha or "<unreadable>",
            )
            continue
        return candidate
    return None


def _preferred_feature_branch(repo_root: Path, **opts: object) -> str:
    for candidate in _candidate_branch_names(repo_root, **opts):
        if candidate and _branch_exists(repo_root, candidate):
            return candidate
    return repo_root.name


def _detached_head_sha(repo_root: Path) -> str | None:
    git_dir = rebase_bookkeeping_dir(repo_root)
    if git_dir is not None:
        head_file = git_dir / "HEAD"
        if head_file.is_file():
            try:
                content = head_file.read_text(encoding="utf-8", errors="replace").strip()
                if (
                    not content.startswith("ref:")
                    and len(content) == _HEX_SHA_LEN
                    and all(c in "0123456789abcdefABCDEF" for c in content)
                ):
                    return content.lower()
            except OSError:
                pass
    try:
        result = run_git(
            ("rev-parse", "--verify", "HEAD"),
            cwd=repo_root,
            label="commit:detached-head-sha",
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    head_sha = result.stdout.strip()
    return head_sha or None


def _direct_branch_tip_matches(common: Path, clean: str, head_sha: str) -> bool:
    ref_file = common / "refs" / "heads" / clean
    if ref_file.is_file():
        with suppress(OSError):
            content = ref_file.read_text(encoding="utf-8", errors="replace").strip()
            if content.lower() == head_sha.lower():
                return True
    packed = common / "packed-refs"
    if packed.is_file():
        with suppress(OSError):
            for line in packed.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("#") or line.startswith("^"):
                    continue
                parts = line.split(maxsplit=1)
                if (
                    len(parts) == _REF_PARTS_COUNT
                    and parts[1] == f"refs/heads/{clean}"
                    and parts[0].lower() == head_sha.lower()
                ):
                    return True
    return False


def _branch_tip_matches(repo_root: Path, branch: str, head_sha: str | None) -> bool:
    if not head_sha:
        return False
    clean = branch.strip()
    if not clean:
        return False
    common = common_git_dir(repo_root) or rebase_bookkeeping_dir(repo_root)
    if common is not None and _direct_branch_tip_matches(common, clean, head_sha):
        return True
    try:
        result = run_git(
            ("rev-parse", "--verify", f"refs/heads/{branch}"),
            cwd=repo_root,
            label="commit:branch-tip-sha",
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    return result.stdout.strip() == head_sha


def _reattach_head_if_detached(repo_root: Path, **opts: object) -> bool:
    from ralph.pipeline._commit_reattach_fallback import (
        create_fresh_branch_for_detached,
        fast_forward_reattach,
        integration_target_branch,
        occupied_worktree_branches,
    )

    if not _is_head_detached(repo_root):
        return False
    branch = _resolve_branch_for_reattach(repo_root, **opts)
    if branch:
        logger.info("Detached HEAD detected; safely reattaching to branch '{}'", branch)
        reattach_head_to_branch(repo_root, branch)
        return True
    detached_head_sha = _detached_head_sha(repo_root)
    integration_target = integration_target_branch(**opts)
    occupied = occupied_worktree_branches(repo_root, **opts)
    if fast_forward_reattach(
        repo_root,
        detached_head_sha=detached_head_sha,
        candidates=_candidate_branch_names(repo_root, **opts),
        branch_exists=_branch_exists,
        integration_target=integration_target,
        occupied=occupied,
    ):
        return True
    fresh = create_fresh_branch_for_detached(
        repo_root,
        detached_head_sha=detached_head_sha,
        candidates=_candidate_branch_names(repo_root, **opts),
        branch_exists=_branch_exists,
        integration_target=integration_target,
        occupied=occupied,
    )
    if fresh:
        logger.info(
            "Detached HEAD reattached to fresh branch '{}' (no safe candidate)",
            fresh,
        )
        reattach_head_to_branch(repo_root, fresh)
        return True
    logger.warning("Detached HEAD detected but target branch could not be resolved")
    return False


reattach_head_if_detached = _reattach_head_if_detached

__all__ = [
    "_branch_exists",
    "_branch_from_worktree_metadata",
    "_branch_tip_matches",
    "_candidate_branch_names",
    "_detached_branch_from_reflog",
    "_detached_head_sha",
    "_head_is_detached",
    "_is_git_worktree",
    "_is_head_detached",
    "_preferred_feature_branch",
    "_read_detached_head_from_git_dir",
    "_reattach_head_if_detached",
    "_resolve_branch_for_reattach",
    "reattach_head_if_detached",
]
