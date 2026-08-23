"""Durable rebase-resolution progress so later stops do not discard landed work."""

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
    """Landed rebase stops and the remaining conflict identity."""

    landed_shas: list[str] = field(default_factory=list)
    remaining_paths: list[str] = field(default_factory=list)
    feature_sha: str | None = None
    target_sha: str | None = None
    stage_oids: list[list[str]] = field(default_factory=list)

    def record_landed(self, sha: str) -> None:
        if sha not in self.landed_shas:
            self.landed_shas.append(sha)


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
