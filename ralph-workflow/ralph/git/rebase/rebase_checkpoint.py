"""Rebase checkpoint persistence and locking utilities."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.rebase._rebase_lock import RebaseLock
from ralph.git.rebase._rebase_phase import RebasePhase

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "LegacyCheckpointInspection",
    "LegacyCheckpointStatus",
    "RebaseCheckpoint",
    "acquire_rebase_lock",
    "clear_rebase_checkpoint",
    "inspect_legacy_rebase_checkpoint",
    "load_rebase_checkpoint",
    "rebase_checkpoint_exists",
    "release_rebase_lock",
    "restore_from_backup",
    "save_rebase_checkpoint",
]

AGENT_DIR = Path(".agent")
CHECKPOINT_FILE = "rebase_checkpoint.json"
BACKUP_SUFFIX = ".bak"
LOCK_FILE = "rebase.lock"
LOCK_TIMEOUT_SECONDS = 1_800


def _current_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _ensure_agent_dir() -> None:
    # filesystem-write-ok: rebase recovery directory is required before durable checkpoint publication
    AGENT_DIR.mkdir(parents=True, exist_ok=True)


def _checkpoint_path() -> Path:
    return AGENT_DIR / CHECKPOINT_FILE


def _backup_path() -> Path:
    return AGENT_DIR / f"{CHECKPOINT_FILE}{BACKUP_SUFFIX}"


def _lock_path() -> Path:
    return AGENT_DIR / LOCK_FILE


def _json_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Checkpoint payload must be a JSON object")

    payload: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("Checkpoint payload keys must be strings")
        payload[key] = item
    return payload


def _load_checkpoint_payload(path: Path) -> dict[str, object]:
    raw_payload: object = json.loads(path.read_text())
    return _json_object(raw_payload)


def _string_list(data: Mapping[str, object], key: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


class LegacyCheckpointStatus(StrEnum):
    """Fail-closed classification for legacy rebase persistence."""

    ABSENT = "absent"
    TERMINAL = "terminal"
    ACTIONABLE_CONFLICT = "actionable_conflict"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LegacyCheckpointInspection:
    """Validated legacy evidence, or a reason it must block startup."""

    status: LegacyCheckpointStatus
    checkpoint: RebaseCheckpoint | None = None
    reason: str | None = None


def _int_value(data: Mapping[str, object], key: str, default: int = 0) -> int:
    value = data.get(key, default)
    match value:
        case int():
            return value
        case str() | bytes() | bytearray():
            try:
                return int(value)
            except ValueError:
                return default
        case _:
            return default


@dataclass
class RebaseCheckpoint:
    """Persisted state for a rebase operation, written to ``.agent/rebase_checkpoint.json``."""

    phase: RebasePhase = field(default_factory=lambda: RebasePhase.NotStarted)
    upstream_branch: str = ""
    conflicted_files: list[str] = field(default_factory=list)
    resolved_files: list[str] = field(default_factory=list)
    error_count: int = 0
    last_error: str | None = None
    timestamp: str = field(default_factory=_current_timestamp)
    phase_error_count: int = 0

    @classmethod
    def new(cls, upstream_branch: str) -> RebaseCheckpoint:
        return cls(upstream_branch=upstream_branch)

    def set_phase(self, phase: RebasePhase) -> None:
        if self.phase != phase:
            self.phase_error_count = 0
        self.phase = phase
        self.timestamp = _current_timestamp()

    def add_conflicted_file(self, file: str) -> None:
        if file not in self.conflicted_files:
            self.conflicted_files.append(file)
            self.timestamp = _current_timestamp()

    def add_resolved_file(self, file: str) -> None:
        if file not in self.resolved_files:
            self.resolved_files.append(file)
            self.timestamp = _current_timestamp()

    def record_error(self, error: str) -> None:
        self.error_count += 1
        self.phase_error_count += 1
        self.last_error = error
        self.timestamp = _current_timestamp()

    def all_conflicts_resolved(self) -> bool:
        return all(file in self.resolved_files for file in self.conflicted_files)

    def unresolved_conflict_count(self) -> int:
        return sum(1 for file in self.conflicted_files if file not in self.resolved_files)

    def to_dict(self) -> dict[str, object]:
        return {
            "phase": self.phase.value,
            "upstream_branch": self.upstream_branch,
            "conflicted_files": list(self.conflicted_files),
            "resolved_files": list(self.resolved_files),
            "error_count": self.error_count,
            "last_error": self.last_error,
            "timestamp": self.timestamp,
            "phase_error_count": self.phase_error_count,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> RebaseCheckpoint:
        """Decode a checkpoint without silently normalizing corrupt evidence."""
        phase_value = data.get("phase")
        if not isinstance(phase_value, str):
            raise ValueError("Checkpoint phase must be a string")
        try:
            phase = RebasePhase(phase_value)
        except ValueError as exc:
            raise ValueError(f"Unknown checkpoint phase: {phase_value!r}") from exc
        upstream = data.get("upstream_branch", "")
        if not isinstance(upstream, str):
            raise ValueError("Checkpoint upstream_branch must be a string")
        for key in ("conflicted_files", "resolved_files"):
            value = data.get(key, [])
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"Checkpoint {key} must be a list of strings")
        for key in ("error_count", "phase_error_count"):
            value = data.get(key, 0)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"Checkpoint {key} must be a non-negative integer")
        timestamp = data.get("timestamp")
        if not isinstance(timestamp, str):
            raise ValueError("Checkpoint timestamp must be a string")
        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Checkpoint timestamp is invalid") from exc
        resolved = list(data.get("resolved_files", []))
        conflicts = list(data.get("conflicted_files", []))
        if any(file not in conflicts for file in resolved):
            raise ValueError("Checkpoint resolved_files must be a subset of conflicted_files")
        if phase not in {RebasePhase.NotStarted, RebasePhase.RebaseComplete, RebasePhase.RebaseAborted} and not upstream:
            raise ValueError("Active checkpoint requires an upstream_branch")
        last_error_value = data.get("last_error")
        if last_error_value is not None and not isinstance(last_error_value, str):
            raise ValueError("Checkpoint last_error must be a string or null")
        return cls(phase=phase, upstream_branch=upstream, conflicted_files=conflicts,
                   resolved_files=resolved, error_count=data.get("error_count", 0),
                   last_error=last_error_value, timestamp=timestamp,
                   phase_error_count=data.get("phase_error_count", 0))


def inspect_legacy_rebase_checkpoint(
    workspace_root: Path | None = None,
) -> LegacyCheckpointInspection:
    """Inspect legacy persistence once, preserving every unsafe byte in place.

    This function never clears, repairs, or replaces a checkpoint.  Startup
    callers use its result as a fail-closed gate before prompt dispatch.
    """
    agent_dir = (workspace_root / AGENT_DIR) if workspace_root is not None else AGENT_DIR
    primary = agent_dir / CHECKPOINT_FILE
    backup = agent_dir / f"{CHECKPOINT_FILE}{BACKUP_SUFFIX}"
    if not primary.exists() and not backup.exists():
        return LegacyCheckpointInspection(LegacyCheckpointStatus.ABSENT)
    try:
        primary_bytes = primary.read_bytes() if primary.exists() else None
        backup_bytes = backup.read_bytes() if backup.exists() else None
        if primary_bytes is None:
            reason = "legacy checkpoint primary is missing"
        elif backup_bytes is not None and primary_bytes != backup_bytes:
            reason = "legacy checkpoint primary and backup disagree"
        else:
            checkpoint = RebaseCheckpoint.from_dict(_load_checkpoint_payload(primary))
            if checkpoint.phase in {RebasePhase.NotStarted, RebasePhase.RebaseComplete, RebasePhase.RebaseAborted}:
                return LegacyCheckpointInspection(LegacyCheckpointStatus.TERMINAL, checkpoint)
            if checkpoint.phase in {RebasePhase.ConflictDetected, RebasePhase.ConflictResolutionInProgress}:
                return LegacyCheckpointInspection(LegacyCheckpointStatus.ACTIONABLE_CONFLICT, checkpoint)
            reason = "legacy rebase recovery is still active"
            return LegacyCheckpointInspection(LegacyCheckpointStatus.BLOCKED, checkpoint, reason)
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        reason = f"legacy checkpoint is unsafe: {exc}"
    return LegacyCheckpointInspection(LegacyCheckpointStatus.BLOCKED, reason=reason)


def save_rebase_checkpoint(checkpoint: RebaseCheckpoint) -> None:
    """Atomically persist ``checkpoint`` to the agent rebase checkpoint file."""
    _ensure_agent_dir()
    path = _checkpoint_path()
    checkpoint_existed = path.exists()

    _backup_checkpoint()

    fd, temp_name = tempfile.mkstemp(
        prefix=f"{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        # filesystem-write-ok: atomic temp+replace target for checkpoint persist
        temp_path.write_text(json.dumps(checkpoint.to_dict(), indent=2), encoding="utf-8")
        # filesystem-write-ok: atomic checkpoint publication after staged durable write
        temp_path.replace(path)
    finally:
        # filesystem-read-ok: failure cleanup only removes staging that this recovery operation created
        if temp_path.exists():
            # filesystem-write-ok: remove uncommitted checkpoint staging on publication failure
            temp_path.unlink()

    if not checkpoint_existed:
        _backup_checkpoint()


def _backup_checkpoint() -> None:
    path = _checkpoint_path()
    # filesystem-read-ok: backup recovery checks whether the dedicated checkpoint exists
    if not path.exists():
        return

    backup = _backup_path()
    fd, temp_name = tempfile.mkstemp(
        prefix=f"{backup.name}.",
        suffix=".tmp",
        dir=backup.parent,
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        # filesystem-write-ok: backup staging preserves rebase recovery bytes before atomic publication
        shutil.copy2(path, temp_path)
        # filesystem-write-ok: atomically publish recovery backup from completed staging copy
        temp_path.replace(backup)
    finally:
        # filesystem-read-ok: failure cleanup only removes staging that this recovery operation created
        if temp_path.exists():
            # filesystem-write-ok: remove uncommitted recovery backup staging after failure
            temp_path.unlink()


def load_rebase_checkpoint() -> RebaseCheckpoint | None:
    """Load and validate the rebase checkpoint, falling back to backup on error."""
    path = _checkpoint_path()
    # filesystem-read-ok: checkpoint recovery checks its single canonical path before loading
    if not path.exists():
        return None

    try:
        payload = _load_checkpoint_payload(path)
        checkpoint = RebaseCheckpoint.from_dict(payload)
        validate_checkpoint(checkpoint)
        return checkpoint
    except (OSError, ValueError, json.JSONDecodeError):
        restored = restore_from_backup()
        if restored is not None:
            return restored
        raise


def clear_rebase_checkpoint() -> None:
    """Delete the rebase checkpoint file if it exists."""
    path = _checkpoint_path()
    if path.exists():
        path.unlink()


def rebase_checkpoint_exists() -> bool:
    """Return True if a rebase checkpoint file exists on disk."""
    return _checkpoint_path().exists()


def validate_checkpoint(checkpoint: RebaseCheckpoint) -> None:
    """Raise ``ValueError`` if ``checkpoint`` contains invalid or inconsistent data."""
    if checkpoint.phase != RebasePhase.NotStarted and not checkpoint.upstream_branch:
        raise ValueError("Checkpoint must contain upstream branch once the rebase starts")

    try:
        datetime.fromisoformat(checkpoint.timestamp)
    except ValueError as exc:
        raise ValueError("Checkpoint has invalid timestamp") from exc

    for resolved in checkpoint.resolved_files:
        if resolved not in checkpoint.conflicted_files:
            raise ValueError("Resolved file missing from conflict list")


def restore_from_backup() -> RebaseCheckpoint | None:
    """Attempt to restore a valid checkpoint from the backup file."""
    backup = _backup_path()
    if not backup.exists():
        return None

    payload = _load_checkpoint_payload(backup)
    checkpoint = RebaseCheckpoint.from_dict(payload)
    validate_checkpoint(checkpoint)
    # filesystem-write-ok: recovery restores a validated rebase checkpoint from its backup
    shutil.copy2(backup, _checkpoint_path())
    return checkpoint


def acquire_rebase_lock() -> None:
    """Acquire the rebase lock file, raising ``OSError`` if another process holds it."""
    _ensure_agent_dir()
    path = _lock_path()
    if path.exists():
        if _is_lock_stale():
            path.unlink()
        else:
            raise OSError("Rebase lock already held")
    # filesystem-write-ok: rebase lock content embeds timestamp + pid; bytes differ per acquisition
    path.write_text(_lock_content())


def release_rebase_lock() -> None:
    """Release the rebase lock file if it exists."""
    path = _lock_path()
    if path.exists():
        path.unlink()


def _lock_content() -> str:
    pid = os.getpid()
    timestamp = _current_timestamp()
    return f"pid={pid}\ntimestamp={timestamp}\n"


def _is_lock_stale() -> bool:
    path = _lock_path()
    try:
        content = path.read_text()
    except OSError:
        return True

    for line in content.splitlines():
        if line.startswith("timestamp="):
            timestamp = line.split("=", 1)[1]
            try:
                cutoff = datetime.fromisoformat(timestamp)
            except ValueError:
                return True
            elapsed = datetime.now(UTC) - cutoff
            return elapsed.total_seconds() > LOCK_TIMEOUT_SECONDS
    return True


RebaseLock._acquire_fn = acquire_rebase_lock
RebaseLock._release_fn = release_rebase_lock
