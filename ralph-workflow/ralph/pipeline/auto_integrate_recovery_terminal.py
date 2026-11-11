
from __future__ import annotations

from contextlib import suppress
from pathlib import Path

from loguru import logger

from ralph.git.rebase.rebase import rebase_in_progress
from ralph.git.subprocess_runner import run_git

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


class TerminalStateViolationError(RuntimeError):
    pass


def post_attempt_verify(
    root: Path,
    *,
    expected_head_sha: str | None,
    owns_resolution: bool,
) -> None:
    git_dir = _rebase_bookkeeping_dir(root)
    if git_dir is None:
        return
    if not owns_resolution:
        for marker in _TERMINAL_MARKER_FILES:
            if marker == "REBASE_HEAD" and not rebase_in_progress(root):
                continue
            if (git_dir / marker).exists():
                raise TerminalStateViolationError(
                    f"terminal-state invariant violated: {marker} present in {git_dir}"
                )
    if expected_head_sha is not None:
        actual = _read_head_sha(root)
        if actual != expected_head_sha:
            raise TerminalStateViolationError(
                "terminal-state invariant violated: HEAD is "
                f"{actual or '<unreadable>'}, expected {expected_head_sha}"
            )


def _read_head_sha(root: Path) -> str | None:
    with suppress(Exception):
        from ralph.git.operations import get_head_sha

        return get_head_sha(root)
    try:
        result = run_git(
            ("rev-parse", "--verify", "HEAD"), cwd=root, label="git-rev-parse-head-verify"
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _rebase_bookkeeping_dir(root: Path) -> Path | None:
    with suppress(Exception):
        from ralph.pipeline._auto_integrate_recovery_git_state import rebase_bookkeeping_dir

        direct = rebase_bookkeeping_dir(root)
        if direct is not None:
            return direct
    try:
        result = run_git(("rev-parse", "--git-dir"), cwd=root, label="git-recovery-git-dir")
    except Exception:
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else root / path


def _head_matches_sha(root: Path, expected_sha: str) -> bool:
    return _read_head_sha(root) == expected_sha


_MIN_QUALIFIED_BACKUP_REF_SEGMENTS = 3


def delete_rebase_backup_refs(root: Path, backup_ref: str | None) -> None:
    if backup_ref is None:
        logger.warning("recovery: no backup ref recorded; legacy or foreign refs left in place")
        return
    if (
        not backup_ref.startswith("refs/rebase-backup/")
        or len(backup_ref.split("/")[2].split("-")) < _MIN_QUALIFIED_BACKUP_REF_SEGMENTS
    ):
        logger.warning(
            "recovery: refusing to delete non-qualified backup ref {} "
            "(legacy SHA-only refs are never deleted)",
            backup_ref,
        )
        return
    try:
        deletion = run_git(
            ("update-ref", "-d", backup_ref),
            cwd=root,
            label=f"recovery:delete-backup-ref:{backup_ref}",
        )
    except Exception as exc:
        logger.warning("recovery: backup-ref deletion failed for {}: {}", backup_ref, exc)
        return
    if deletion.returncode not in (0, 1):
        logger.warning("recovery: backup-ref deletion failed for {}", backup_ref)
