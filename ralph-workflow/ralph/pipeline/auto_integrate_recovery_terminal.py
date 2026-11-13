from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.rebase.rebase import rebase_in_progress
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.terminal_state_violation_error import TerminalStateViolationError

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord

__all__ = (
    "RESOLUTION_TERMINAL_MARKER_FIELD",
    "RESUME_COMMAND",
    "ResolutionAttemptKey",
    "ResolutionTerminalMarker",
    "ResolutionTerminalMarkerValue",
    "TerminalStateViolationError",
    "delete_rebase_backup_refs",
    "post_attempt_verify",
    "resolution_terminal_marker",
    "terminal_marker_blocks_attempt",
)

type ResolutionAttemptKey = tuple[str | None, tuple[str, ...], str | None]
type ResolutionTerminalMarkerValue = tuple[ResolutionAttemptKey | None, str, str]

RESOLUTION_TERMINAL_MARKER_FIELD = "resolution_terminal_marker"
RESUME_COMMAND = "ralph --resume"
_MARKER_PARTS = 3


@dataclass(frozen=True, slots=True)
class ResolutionTerminalMarker:
    attempt_key: ResolutionAttemptKey | None
    state: str
    resume_command: str

    def as_record_value(self) -> ResolutionTerminalMarkerValue:
        return (self.attempt_key, self.state, self.resume_command)


def resolution_terminal_marker(record: IntegrationRecord) -> ResolutionTerminalMarker | None:
    values = record.model_dump(mode="python")
    raw = values.get(RESOLUTION_TERMINAL_MARKER_FIELD)
    if isinstance(raw, ResolutionTerminalMarker):
        return raw
    if not isinstance(raw, (tuple, list)) or len(raw) != _MARKER_PARTS:
        return None
    attempt_key, state, resume_command = raw
    if not isinstance(state, str) or not isinstance(resume_command, str):
        return None
    parsed_key = _resolution_attempt_key(attempt_key)
    if attempt_key is not None and parsed_key is None:
        return None
    return ResolutionTerminalMarker(parsed_key, state, resume_command)


def _resolution_attempt_key(raw: object) -> ResolutionAttemptKey | None:
    if raw is None:
        return None
    if not isinstance(raw, (tuple, list)) or len(raw) != _MARKER_PARTS:
        return None
    stop, agents, config = raw
    valid = (
        (stop is None or isinstance(stop, str))
        and isinstance(agents, (tuple, list))
        and all(isinstance(agent, str) for agent in agents)
        and (config is None or isinstance(config, str))
    )
    return (stop, tuple(agents), config) if valid else None


def terminal_marker_blocks_attempt(
    record: IntegrationRecord,
    attempt_key: ResolutionAttemptKey | None,
) -> bool:
    marker = resolution_terminal_marker(record)
    return marker is not None and marker.attempt_key == attempt_key


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
