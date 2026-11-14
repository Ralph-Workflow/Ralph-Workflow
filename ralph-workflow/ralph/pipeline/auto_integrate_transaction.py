from __future__ import annotations

import json
import os
import socket
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from ralph.git.merge import branch_sha, is_ancestor
from ralph.git.subprocess_runner import run_git
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import atomic_write_bytes_if_changed
from ralph.pipeline.auto_integrate_catchup_coordination import _try_lock, _unlock
from ralph.pipeline.lease_outcome_kind import LeaseOutcomeKind
from ralph.pipeline.lease_unknown_reason import LeaseUnknownReason

if TYPE_CHECKING:
    from collections.abc import Iterator


LOCK_FILENAME: Final = "ralph-auto-integrate.lock"
LOCK_OWNER_FILENAME: Final = "ralph-auto-integrate.lock-owner.json"
OPERATION_MARKER_FILENAME: Final = "ralph-auto-integrate-operation.json"
STALE_LOCK_AGE_SECONDS: Final[float] = 600.0
_RECORD_VERSION: Final[int] = 1


@dataclass(frozen=True, slots=True)
class _OperationFacts:
    operation_id: str
    target: str
    target_tip: str
    feature_tip: str
    completed_steps: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LeaseOutcome:
    kind: LeaseOutcomeKind
    reason: LeaseUnknownReason | None = None
    evidence: str | None = None
    stale_lock_recovered: bool = False
    resumed_operation: bool = False
    sibling_landed: bool = False

    @property
    def acquired(self) -> bool:
        return self.kind is LeaseOutcomeKind.ACQUIRED

    def __bool__(self) -> bool:
        return self.acquired

    def describe(self, busy_message: str) -> str:
        if self.kind is LeaseOutcomeKind.HELD_BY_LIVE_OWNER:
            return busy_message
        reason = self.reason.name if self.reason is not None else "UNCLASSIFIED"
        detail = f"; {self.evidence}" if self.evidence else ""
        return (
            f"integration lease state unknown ({reason}{detail}); "
            "backing off and retrying without dispatching"
        )

    @classmethod
    def acquired_outcome(
        cls,
        *,
        stale_lock_recovered: bool = False,
        resumed_operation: bool = False,
        sibling_landed: bool = False,
    ) -> LeaseOutcome:
        return cls(
            kind=LeaseOutcomeKind.ACQUIRED,
            stale_lock_recovered=stale_lock_recovered,
            resumed_operation=resumed_operation,
            sibling_landed=sibling_landed,
        )

    @classmethod
    def held_by_live_owner(cls) -> LeaseOutcome:
        return cls(kind=LeaseOutcomeKind.HELD_BY_LIVE_OWNER)

    @classmethod
    def unknown(cls, reason: LeaseUnknownReason, evidence: str | None = None) -> LeaseOutcome:
        return cls(kind=LeaseOutcomeKind.UNKNOWN, reason=reason, evidence=evidence)


def _git_dir(root: Path) -> tuple[Path | None, str | None]:
    observed = run_git(("rev-parse", "--absolute-git-dir"), cwd=root, label="integration:lock-dir")
    if observed.returncode != 0 or not observed.stdout.strip():
        evidence = (observed.stderr or "").strip() or f"rc={observed.returncode}"
        return None, evidence
    return Path(observed.stdout.strip()), None


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    atomic_write_bytes_if_changed(
        DEFAULT_FILE_BACKEND,
        path,
        encoded,
        tmp_path=path.with_suffix(path.suffix + ".staging"),
        sync_directory=True,
        prepare_write=lambda: DEFAULT_FILE_BACKEND.mkdir(path.parent, parents=True, exist_ok=True),
    )


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        parsed = cast("object", json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _owner_is_stale(path: Path, now: float) -> bool:
    payload = _read_json(path)
    if payload is None or payload.get("released") is True:
        return False
    recorded_at = payload.get("recorded_at")
    age = now - float(recorded_at) if isinstance(recorded_at, (int, float)) else 0.0
    hostname = payload.get("hostname")
    pid = payload.get("pid")
    if hostname == socket.gethostname() and isinstance(pid, int) and not isinstance(pid, bool):
        return not _process_alive(pid)
    return age >= STALE_LOCK_AGE_SECONDS


def _write_owner(path: Path, now: float, *, released: bool) -> None:
    _atomic_json(
        path,
        {
            "version": _RECORD_VERSION,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "recorded_at": now,
            "released": released,
        },
    )


def operation_marker_path(root: Path) -> Path | None:
    git_dir, _evidence = _git_dir(root)
    return None if git_dir is None else git_dir / OPERATION_MARKER_FILENAME


def begin_integration_operation(
    root: Path,
    *,
    operation_id: str,
    target: str,
    target_tip: str,
    feature_tip: str,
) -> None:
    marker = operation_marker_path(root)
    if marker is None:
        raise RuntimeError("integration operation marker requires a resolvable Git directory")
    _atomic_json(
        marker,
        {
            "version": _RECORD_VERSION,
            "operation_id": operation_id,
            "target": target,
            "target_tip": target_tip,
            "feature_tip": feature_tip,
            "completed_steps": [],
        },
    )


def record_integration_step(root: Path, step: str) -> None:
    marker = operation_marker_path(root)
    if marker is None:
        raise RuntimeError("integration operation marker requires a resolvable Git directory")
    facts = _read_operation(marker)
    if facts is None:
        raise RuntimeError("integration operation marker is absent or invalid")
    steps = facts.completed_steps if step in facts.completed_steps else (*facts.completed_steps, step)
    _write_operation(marker, facts, steps)


def finish_integration_operation(root: Path) -> None:
    marker = operation_marker_path(root)
    if marker is not None:
        DEFAULT_FILE_BACKEND.unlink(marker, missing_ok=True)


def _write_operation(path: Path, facts: _OperationFacts, steps: tuple[str, ...]) -> None:
    _atomic_json(
        path,
        {
            "version": _RECORD_VERSION,
            "operation_id": facts.operation_id,
            "target": facts.target,
            "target_tip": facts.target_tip,
            "feature_tip": facts.feature_tip,
            "completed_steps": list(steps),
        },
    )


def _read_operation(path: Path) -> _OperationFacts | None:
    payload = _read_json(path)
    if payload is None or payload.get("version") != _RECORD_VERSION:
        return None
    operation_id = payload.get("operation_id")
    target = payload.get("target")
    target_tip = payload.get("target_tip")
    feature_tip = payload.get("feature_tip")
    raw_steps = payload.get("completed_steps", [])
    if not all(isinstance(value, str) for value in (operation_id, target, target_tip, feature_tip)):
        return None
    if not isinstance(raw_steps, list) or not all(isinstance(step, str) for step in raw_steps):
        return None
    return _OperationFacts(
        operation_id=cast("str", operation_id),
        target=cast("str", target),
        target_tip=cast("str", target_tip),
        feature_tip=cast("str", feature_tip),
        completed_steps=tuple(cast("list[str]", raw_steps)),
    )


def completed_integration_steps(root: Path) -> tuple[str, ...]:
    marker = operation_marker_path(root)
    facts = None if marker is None else _read_operation(marker)
    return () if facts is None else facts.completed_steps


def _sibling_landed(root: Path, facts: _OperationFacts) -> bool:
    current_target = branch_sha(root, facts.target)
    if current_target is None or current_target == facts.target_tip:
        return False
    return is_ancestor(root, facts.feature_tip, current_target)


@contextmanager
def integration_transaction(root: Path) -> Iterator[LeaseOutcome]:
    git_marker = root / ".git"
    if not git_marker.exists():
        yield LeaseOutcome.acquired_outcome()
        return
    if git_marker.is_dir() and not (git_marker / "HEAD").exists():
        yield LeaseOutcome.unknown(
            LeaseUnknownReason.GIT_DIR_INVALID,
            evidence=str(git_marker),
        )
        return
    git_dir, evidence = _git_dir(root)
    if git_dir is None:
        yield LeaseOutcome.unknown(
            LeaseUnknownReason.GIT_DIR_UNRESOLVED,
            evidence=evidence,
        )
        return
    path = git_dir / LOCK_FILENAME
    owner_path = git_dir / LOCK_OWNER_FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+", encoding="utf-8")
    except OSError as error:
        yield LeaseOutcome.unknown(LeaseUnknownReason.LOCK_IO_ERROR, evidence=str(error.errno))
        return
    with handle:
        acquired = _try_lock(handle)
        if not acquired:
            yield LeaseOutcome.held_by_live_owner()
            return
        now = time.time()
        stale = _owner_is_stale(owner_path, now)
        marker = git_dir / OPERATION_MARKER_FILENAME
        operation = _read_operation(marker)
        landed = operation is not None and _sibling_landed(root, operation)
        if landed:
            DEFAULT_FILE_BACKEND.unlink(marker, missing_ok=True)
        _write_owner(owner_path, now, released=False)
        outcome = LeaseOutcome.acquired_outcome(
            stale_lock_recovered=stale,
            resumed_operation=operation is not None,
            sibling_landed=landed,
        )
        try:
            yield outcome
        finally:
            _write_owner(owner_path, time.time(), released=True)
            _unlock(handle)


__all__ = [
    "LeaseOutcome",
    "begin_integration_operation",
    "completed_integration_steps",
    "finish_integration_operation",
    "integration_transaction",
    "operation_marker_path",
    "record_integration_step",
]
