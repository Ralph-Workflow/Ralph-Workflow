"""Durable rebase-resolution progress so later stops do not discard landed work.

The sidecar describes ONE rebase and now says which one on disk.
Callers never read it by path alone: they read it through
:func:`load_progress_for_rebase`, passing the identity of the rebase
currently paused in the worktree, and a file written for any OTHER
rebase is deleted rather than returned.

That scoping is load-bearing, not tidiness.
:func:`ralph.pipeline.auto_integrate_rebase_merge._rebase_has_landed_stops`
asks this module whether aborting a paused rebase would discard replay
commits an agent already resolved, and it answers ``True`` by LEAVING
THE CONFLICTED REBASE ON DISK for a later resume. An unstamped
sidecar made that answer permanently ``True`` for the whole worktree:
the first agent-resolved rebase wrote landed SHAs, nothing ever
removed them, and every later unrelated conflicted rebase was then
left paused instead of aborted -- one wedged worktree per conflict,
waiting on a resume that can never come for a rebase the file does not
describe.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed

if TYPE_CHECKING:
    from pathlib import Path

_PROGRESS_NAME = "conflict-resolution-progress.json"


@dataclass(slots=True)
class RebaseResolutionProgress:
    """Landed rebase stops and the identity of the rebase that landed them.

    ``feature_sha`` / ``target_sha`` are the paused rebase's ``orig-head``
    and ``onto`` -- the two SHAs git pins for the whole replay, so they
    name this rebase and no other. They are what
    :func:`load_progress_for_rebase` matches against; a record carrying
    neither is a file from before the stamp existed and is treated as
    belonging to no rebase at all.
    """

    landed_shas: list[str] = field(default_factory=list)
    remaining_paths: list[str] = field(default_factory=list)
    feature_sha: str | None = None
    target_sha: str | None = None
    stage_oids: list[list[str]] = field(default_factory=list)

    def record_landed(self, sha: str) -> None:
        if sha not in self.landed_shas:
            self.landed_shas.append(sha)

    def describes_rebase(self, feature_sha: str | None, target_sha: str | None) -> bool:
        """Whether this record was written for the ``(feature, target)`` rebase.

        Exact match on BOTH SHAs, and ``None`` on either side never
        matches. The permissive direction some other identity checks in
        Ralph take (an unreadable side compares as equal, preserving a
        budget) would be the wrong one here: the caller spends a match
        by leaving a conflicted rebase paused on disk, so guessing
        "same rebase" strands the repository, while guessing
        "different rebase" costs at most a replay git's reflog still
        holds.
        """
        if feature_sha is None or target_sha is None:
            return False
        return self.feature_sha == feature_sha and self.target_sha == target_sha


def progress_path(root: Path) -> Path:
    """Return the sidecar path Ralph uses to persist landed rebase stops."""
    return root / ".ralph" / _PROGRESS_NAME


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _oid_rows(value: object) -> list[list[str]]:
    if not isinstance(value, list):
        return []
    return [[str(part) for part in row] for row in value if isinstance(row, list)]


def load_progress(root: Path) -> RebaseResolutionProgress | None:
    """Load persisted rebase-resolution progress, or None when missing or corrupt."""
    path = progress_path(root)
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    landed = parsed.get("landed_shas")
    if not isinstance(landed, list):
        return None
    feature = parsed.get("feature_sha")
    target = parsed.get("target_sha")
    return RebaseResolutionProgress(
        landed_shas=[str(item) for item in landed],
        remaining_paths=_string_list(parsed.get("remaining_paths", [])),
        feature_sha=feature if isinstance(feature, str) else None,
        target_sha=target if isinstance(target, str) else None,
        stage_oids=_oid_rows(parsed.get("stage_oids", [])),
    )


def load_progress_for_rebase(
    root: Path,
    *,
    feature_sha: str | None,
    target_sha: str | None,
) -> RebaseResolutionProgress | None:
    """Sidecar progress for the rebase ``(feature_sha, target_sha)`` names.

    The ONLY way callers should read the sidecar. A file that describes
    a different rebase -- or, for a file written before the identity was
    stamped, no rebase at all -- is not merely ignored: it is DELETED
    before returning ``None``, so a worktree self-heals on the first
    read rather than carrying a booby-trapped record until someone
    notices. Deleting is safe because a record that cannot be matched to
    the rebase in progress can never be resumed from either.

    Args:
        root: Worktree that owns the sidecar.
        feature_sha: ``orig-head`` of the rebase asking, or ``None``
            when it could not be read.
        target_sha: ``onto`` of the rebase asking, or ``None`` when it
            could not be read.

    Returns:
        The record when it describes exactly that rebase, else ``None``.
    """
    progress = load_progress(root)
    if progress is None:
        return None
    if progress.describes_rebase(feature_sha, target_sha):
        return progress
    clear_progress(root)
    return None


def save_progress(root: Path, progress: RebaseResolutionProgress) -> None:
    """Write landed stops and remaining conflict identity to the sidecar."""
    path = progress_path(root)
    DEFAULT_FILE_BACKEND.mkdir(path.parent, parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "landed_shas": list(progress.landed_shas),
        "remaining_paths": list(progress.remaining_paths),
        "feature_sha": progress.feature_sha,
        "target_sha": progress.target_sha,
        "stage_oids": list(progress.stage_oids),
    }
    write_text_if_changed(
        DEFAULT_FILE_BACKEND,
        path,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def clear_progress(root: Path) -> None:
    """Delete the progress sidecar when a rebase identity is no longer valid."""
    path = progress_path(root)
    try:
        path.unlink()
    except OSError:
        return
