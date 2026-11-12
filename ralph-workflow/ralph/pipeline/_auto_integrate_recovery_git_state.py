from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Final

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
    try:
        from ralph.git.operations import _resolve_gitdir_paths

        gitdir, _commondir = _resolve_gitdir_paths(root)
        if gitdir is not None and gitdir.is_dir():
            return gitdir
    except (OSError, ValueError) as exc:
        logger.debug("direct rebase bookkeeping dir lookup failed: {}", exc)
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
    try:
        from ralph.git.operations import _resolve_gitdir_paths

        gitdir, commondir = _resolve_gitdir_paths(root)
        target = commondir or gitdir
        if target is not None and target.is_dir():
            return target
    except (OSError, ValueError) as exc:
        logger.debug("direct common git dir lookup failed: {}", exc)
    try:
        result = run_git(("rev-parse", "--git-common-dir"), cwd=root, label="git-common-dir")
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    common = Path(result.stdout.strip())
    return common if common.is_absolute() else (root / common).resolve()


def common_dir_lock_paths(root: Path) -> tuple[Path, ...]:
    common_dir = common_git_dir(root)
    if common_dir is None:
        return ()
    ref_dir = common_dir / "refs"
    try:
        ref_locks = tuple(ref_dir.rglob("*.lock")) if ref_dir.is_dir() else ()
    except OSError:
        ref_locks = ()
    return (common_dir / "HEAD.lock", common_dir / "packed-refs.lock", *ref_locks)


def select_rebase_state_dir(git_dir: Path) -> Path | None:
    if (git_dir / "rebase-merge").exists():
        return git_dir / "rebase-merge"
    if (git_dir / "rebase-apply").exists():
        return git_dir / "rebase-apply"
    return None


def remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        import shutil

        try:
            shutil.rmtree(path)
        except Exception as exc:
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
    from ralph.pipeline.auto_integrate_recovery_lock import (
        _lock_holder_is_dead as _impl,
    )

    return _impl(lock_path, gitdir=gitdir, gitdir_common=gitdir_common)


def rebase_state_dir_is_corrupt(state_dir: Path) -> bool:
    if not state_dir.is_dir():
        return False
    return not (state_dir / "head-name").exists() and not (state_dir / "onto").exists()


def run_sequencer_quit(root: Path, op: str) -> None:
    try:
        run_git(
            (op, "--quit"),
            cwd=root,
            label=f"recovery:sequencer-quit:{op}",
        )
    except Exception as exc:
        logger.warning("recovery: sequencer --quit for '{}' failed: {}", op, exc)


def detached_head_no_state(root: Path, git_dir: Path) -> bool:
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


_HEX_SHA_LEN: Final[int] = 40
_REF_SPLIT_PARTS: Final[int] = 2


def _resolve_reflog_candidate_direct(common: Path, git_dir: Path, candidate: str) -> str | None:
    clean = candidate.strip()
    ref_file = common / "refs" / "heads" / clean
    candidate_tip: str | None = None
    if ref_file.is_file():
        with contextlib.suppress(OSError):
            candidate_tip = ref_file.read_text(encoding="utf-8", errors="replace").strip()
    if not candidate_tip:
        packed = common / "packed-refs"
        if packed.is_file():
            with contextlib.suppress(OSError):
                for line in packed.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("#") or line.startswith("^"):
                        continue
                    parts = line.split(maxsplit=1)
                    if len(parts) == _REF_SPLIT_PARTS and parts[1] == f"refs/heads/{clean}":
                        candidate_tip = parts[0].strip()
                        break
    head_file = git_dir / "HEAD"
    head_tip: str | None = None
    if head_file.is_file():
        with contextlib.suppress(OSError):
            c = head_file.read_text(encoding="utf-8", errors="replace").strip()
            if (
                not c.startswith("ref:")
                and len(c) == _HEX_SHA_LEN
                and all(ch in "0123456789abcdefABCDEF" for ch in c)
            ):
                head_tip = c.lower()
    if candidate_tip and head_tip and candidate_tip.lower() == head_tip.lower():
        return candidate
    return None


def _resolve_reflog_candidate(root: Path, candidate: str | None) -> str | None:
    if not candidate:
        return None
    common = common_git_dir(root) or rebase_bookkeeping_dir(root)
    git_dir = rebase_bookkeeping_dir(root) or (root / ".git")
    if common is not None and git_dir is not None:
        direct = _resolve_reflog_candidate_direct(common, git_dir, candidate)
        if direct is not None:
            return direct
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
    return candidate if candidate_tip == detached_head_oid else None


def _is_checkout_or_switch_subject(subject: str) -> bool:
    return ("checkout: moving from " in subject) or ("switch branch to " in subject)


def _parse_checkout_destination(subject: str) -> str | None:
    for marker in ("checkout: moving from ", "switch branch to "):
        idx = subject.find(marker)
        if idx == -1:
            continue
        rest = subject[idx + len(marker) :]
        parts = rest.split(" to ", 1)
        source_dest: tuple[str, str] = ("", "")
        if len(parts) != len(source_dest):
            return None
        return parts[1].strip() or None
    return None


def _read_reflog_checkout_subject(git_dir: Path) -> str | None:
    reflog_path = git_dir / "logs" / "HEAD"
    if not reflog_path.is_file():
        return None
    with contextlib.suppress(OSError):
        lines = reflog_path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in reversed(lines[-50:]):
            parts = line.split("\t", 1)
            if len(parts) == _REF_SPLIT_PARTS:
                subject = parts[1].strip()
                if not _is_checkout_or_switch_subject(subject):
                    continue
                return _parse_checkout_destination(subject)
    return None


def detached_branch_name(root: Path) -> str | None:
    git_dir = rebase_bookkeeping_dir(root)
    if git_dir is not None:
        candidate = _read_reflog_checkout_subject(git_dir)
        if candidate is not None:
            resolved = _resolve_reflog_candidate(root, candidate)
            if resolved is not None:
                return resolved
    try:
        result = run_git(
            ("reflog", "--format=%H%x00%gs", "-n", "50"),
            cwd=root,
            label="recovery:head-reflog",
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        if "\x00" not in line:
            continue
        _sha, subject = line.split("\x00", 1)
        if not _is_checkout_or_switch_subject(subject):
            continue

        candidate = _parse_checkout_destination(subject)
        return _resolve_reflog_candidate(root, candidate)
    return None


def reattach_head_to_branch(root: Path, branch: str) -> None:
    try:
        run_git(
            ("symbolic-ref", "HEAD", f"refs/heads/{branch}"),
            cwd=root,
            label="recovery:head-reattach",
        )
    except Exception as exc:
        logger.warning("recovery: could not re-attach HEAD to '{}': {}", branch, exc)
