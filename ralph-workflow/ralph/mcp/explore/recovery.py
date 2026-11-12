from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ralph.mcp.explore._bounded_state import BoundedKeyValueStore
from ralph.mcp.explore._recovery_types import HealthState, _RecoveryState

logger = logging.getLogger(__name__)


DEFAULT_BACKOFF_BASE_SECONDS: float = 0.5
DEFAULT_MAX_BACKOFF_SECONDS: float = 8.0
DEFAULT_MAX_ATTEMPTS: int = 4


_RECOVERY_STATE_FILENAME: str = "recovery_state.json"


def _state_file(workspace_root: Path) -> Path:
    return Path(workspace_root) / ".agent" / "ralph-explore" / _RECOVERY_STATE_FILENAME


def _read_state_file(workspace_root: Path) -> _RecoveryState | None:
    path = _state_file(workspace_root)
    if not path.is_file():
        return None
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    code = raw.get("last_failure_code")
    message = raw.get("last_failure_message")
    health_raw = raw.get("health", "healthy")
    try:
        health = HealthState(str(health_raw))
    except ValueError:
        health = HealthState.HEALTHY
    attempts = raw.get("recovery_attempts", 0)
    next_at = raw.get("next_recovery_at")
    cooldown = raw.get("cooldown_remaining", 0.0)
    state = _RecoveryState(
        health=health,
        last_failure_code=str(code) if code else None,
        last_failure_message=str(message) if message else None,
        recovery_attempts=int(attempts) if isinstance(attempts, (int, float)) else 0,
        next_recovery_at=float(next_at) if isinstance(next_at, (int, float)) else None,
        cooldown_remaining=float(cooldown) if isinstance(cooldown, (int, float)) else 0.0,
    )
    return state


def _write_state_file(workspace_root: Path, state: _RecoveryState) -> None:
    path = _state_file(workspace_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    payload: dict[str, object] = {
        "health": state.health.value,
        "last_failure_code": state.last_failure_code,
        "last_failure_message": state.last_failure_message,
        "recovery_attempts": state.recovery_attempts,
        "next_recovery_at": state.next_recovery_at,
        "cooldown_remaining": state.cooldown_remaining,
    }
    try:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        tmp_path.replace(path)
    except OSError:
        return


@dataclass
class RecoveryScheduler:
    workspace_root: Path
    initial_attempts: int = 0
    initial_health: HealthState = HealthState.HEALTHY
    backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS
    max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _state: _RecoveryState = field(default_factory=_RecoveryState)
    _load_persisted: bool = True

    def __post_init__(self) -> None:
        if self._load_persisted:
            persisted = _read_state_file(self.workspace_root)
            if persisted is not None:
                self._state = persisted

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "health": self._state.health.value,
                "last_failure": (
                    {
                        "code": self._state.last_failure_code,
                        "message": self._state.last_failure_message,
                    }
                    if self._state.last_failure_code
                    else None
                ),
                "recovery_attempts": self._state.recovery_attempts,
                "next_recovery_at": self._state.next_recovery_at,
                "cooldown_remaining": self._state.cooldown_remaining,
            }

    @property
    def health(self) -> HealthState:
        with self._lock:
            return self._state.health

    def mark_healthy(self) -> None:
        with self._lock:
            self._state.health = HealthState.HEALTHY
            self._state.last_failure_code = None
            self._state.last_failure_message = None
            self._state.cooldown_remaining = 0.0
            self._state.next_recovery_at = None
        self._persist()

    def mark_building(self) -> None:
        with self._lock:
            self._state.health = HealthState.BUILDING
            self._state.cooldown_remaining = 0.0
        self._persist()

    def record_failure(
        self,
        *,
        code: str,
        message: str = "",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        with self._lock:
            self._state.recovery_attempts += 1
            self._state.last_failure_code = code
            self._state.last_failure_message = message
            if self._state.recovery_attempts > self.max_attempts:
                self._state.health = HealthState.UNHEALTHY
                self._state.next_recovery_at = None
                self._state.cooldown_remaining = 0.0
                self._persist()
                return
            backoff = min(
                self.max_backoff_seconds,
                self.backoff_base_seconds * float(1 << max(0, self._state.recovery_attempts - 1)),
            )
            self._state.health = HealthState.STALE
            self._state.next_recovery_at = clock() + backoff
            self._state.cooldown_remaining = backoff
        self._persist()

    def mark_read_only(self) -> None:
        with self._lock:
            self._state.health = HealthState.DEGRADED
        self._persist()

    def mark_stale(self) -> None:
        with self._lock:
            self._state.health = HealthState.STALE
        self._persist()

    def observe(
        self,
        *,
        code: str,
        message: str = "",
        health: HealthState = HealthState.STALE,
    ) -> None:
        with self._lock:
            self._state.health = (
                health
                if self._state.recovery_attempts <= self.max_attempts
                else HealthState.UNHEALTHY
            )
            self._state.last_failure_code = code
            self._state.last_failure_message = message
        self._persist()

    def should_attempt_recovery(self, *, clock: Callable[[], float] = time.monotonic) -> bool:
        with self._lock:
            if self._state.recovery_attempts > self.max_attempts:
                return False
            if self._state.next_recovery_at is None:
                return True
            return clock() >= self._state.next_recovery_at

    def _persist(self) -> None:
        with self._lock:
            snapshot = _RecoveryState(
                health=self._state.health,
                last_failure_code=self._state.last_failure_code,
                last_failure_message=self._state.last_failure_message,
                recovery_attempts=self._state.recovery_attempts,
                next_recovery_at=self._state.next_recovery_at,
                cooldown_remaining=self._state.cooldown_remaining,
            )
        _write_state_file(self.workspace_root, snapshot)

    def clear_persisted(self) -> None:
        path = _state_file(self.workspace_root)
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass


_LAST_ADVISORY_LOCK_FD: deque[int | None] = deque([None], maxlen=1)


def advisory_lock_path(workspace_root: Path) -> Path:
    return Path(workspace_root) / ".agent" / "ralph-explore" / ".recovery.lock"


def try_advisory_lock(workspace_root: Path) -> bool:
    lock_path = advisory_lock_path(workspace_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            os.close(fd)
            return False
    except ImportError:
        pass
    import contextlib

    with contextlib.suppress(OSError):
        lock_path.touch()
    with contextlib.suppress(OSError):
        lock_path.stat()
    _LAST_ADVISORY_LOCK_FD[0] = fd
    return True


def release_advisory_lock() -> None:
    import contextlib

    fd = _LAST_ADVISORY_LOCK_FD[0]
    if fd is None:
        return
    _LAST_ADVISORY_LOCK_FD[0] = None
    try:
        import fcntl

        with contextlib.suppress(OSError, ValueError):
            fcntl.flock(fd, fcntl.LOCK_UN)
    except ImportError:
        pass
    with contextlib.suppress(OSError, ValueError):
        os.close(fd)


def build_scheduler(workspace_root: Path) -> RecoveryScheduler:
    return RecoveryScheduler(workspace_root=workspace_root)


def run_recovery_action(
    scheduler: RecoveryScheduler,
    *,
    workspace_root: Path,
    fault_code: str,
    reason: str = "",
    timeout_ms: int = 5_000,
) -> dict[str, object]:
    import time as _time

    from ralph.mcp.explore.pipeline import ReindexOptions, reindex
    from ralph.mcp.explore.store import DEFAULT_INDEX_ROOT, ExploreStore

    snapshot: dict[str, object] = {}
    code = str(fault_code or "").strip()
    if not code:
        scheduler.mark_healthy()
        return scheduler.snapshot()

    scheduler.mark_building()
    index_dir = Path(workspace_root) / DEFAULT_INDEX_ROOT
    lock_acquired = False
    try:
        try:
            lock_acquired = try_advisory_lock(workspace_root)
        except OSError:
            lock_acquired = False
        if not lock_acquired:
            scheduler.record_failure(code=code, message="lock_held_by_other_session")
            return scheduler.snapshot()

        if code in {"index_unwritable", "index_read_only"}:
            scheduler.record_failure(code=code, message=reason)
            return scheduler.snapshot()

        if code in {"interrupted_build", "workspace_moved", "version_mismatch", "index_corrupt"}:
            import shutil

            try:
                if index_dir.exists():
                    shutil.rmtree(index_dir, ignore_errors=True)
            except OSError:
                pass

        store = ExploreStore(index_dir)
        try:
            raw_committed = ""
            try:
                raw_committed = store.get_setting("current_generation") or "0"
            except Exception:
                raw_committed = "0"
            try:
                committed = int(raw_committed)
            except (TypeError, ValueError):
                committed = 0
            full_mode_codes = {
                "interrupted_build",
                "workspace_moved",
                "version_mismatch",
                "index_corrupt",
            }
            use_mode = "full" if code in full_mode_codes or committed <= 0 else "changed"
            options = ReindexOptions(
                mode=use_mode,
                timeout_ms=max(1_000, int(timeout_ms)),
            )
            start = _time.monotonic()
            result = reindex(store, Path(workspace_root), options=options)
            elapsed = _time.monotonic() - start
            status_value: object = getattr(result, "status", "ok")
            status = str(status_value) if status_value is not None else "ok"
            if status in {"ok", "skipped_no_changes"}:
                scheduler.mark_healthy()
                scheduler.clear_persisted()
            else:
                scheduler.record_failure(
                    code=code,
                    message=f"reindex_status={status} reason={reason}",
                )
            snapshot = {
                "rebuild_status": status,
                "rebuild_wall_seconds": elapsed,
            }
        except Exception as exc:
            scheduler.record_failure(code=code, message=f"exception: {exc!r}")
            snapshot = {"rebuild_status": "error", "rebuild_wall_seconds": 0.0}
        finally:
            with contextlib.suppress(Exception):
                store.close()
    finally:
        if lock_acquired:
            with contextlib.suppress(Exception):
                release_advisory_lock()
    snapshot_payload = scheduler.snapshot()
    snapshot_payload.update(snapshot)
    return snapshot_payload


_PENDING_RECOVERY_CAP: int = 64
_PENDING_RECOVERY: BoundedKeyValueStore[str, str] = BoundedKeyValueStore()
_PENDING_LOCK = threading.Lock()

_RECOVERABLE_QUERY_CODES: frozenset[str] = frozenset(
    {
        "no_committed_generation",
        "version_mismatch",
        "index_corrupt",
        "interrupted_build",
        "index_stale_scope",
        "ignore_rule_changed",
        "timeout_exceeded",
        "indexer_error",
        "workspace_moved",
        "index_unwritable",
        "index_read_only",
    }
)


def _pending_key(workspace_root: Path) -> str:
    return str(Path(workspace_root).resolve())


def _committed_generation_healthy(workspace_root: Path) -> bool:
    from ralph.mcp.explore.store import DEFAULT_INDEX_ROOT, ExploreStore

    index_dir = Path(workspace_root) / DEFAULT_INDEX_ROOT
    if not (index_dir / "index.sqlite").is_file():
        return False
    try:
        store = ExploreStore(index_dir)
    except Exception:
        return False
    try:
        raw: object = store.get_setting("current_generation")
        latest = store.latest_job()
    except Exception:
        return False
    finally:
        with contextlib.suppress(Exception):
            store.close()
    try:
        generation = int(str(raw or "0"))
    except (TypeError, ValueError):
        return False
    if generation <= 0:
        return False
    if latest is None:
        return True
    keys = tuple(latest.keys())
    if "status" not in keys:
        return False
    raw_status: object = latest["status"]
    return str(raw_status) == "ok"


def enqueue_recovery(
    workspace_root: Path,
    code: str,
    *,
    message: str = "",
) -> None:
    if code not in _RECOVERABLE_QUERY_CODES:
        return
    health = HealthState.STALE
    if code in {"no_committed_generation", "timeout_exceeded", "interrupted_build"}:
        health = HealthState.BUILDING
    elif code in {"index_unwritable", "index_read_only", "index_corrupt"}:
        health = HealthState.DEGRADED
    try:
        build_scheduler(workspace_root).observe(code=code, message=message or code, health=health)
    except OSError:
        return
    key = _pending_key(workspace_root)
    with _PENDING_LOCK:
        if key not in _PENDING_RECOVERY and len(_PENDING_RECOVERY) >= _PENDING_RECOVERY_CAP:
            oldest = next(iter(_PENDING_RECOVERY))
            _PENDING_RECOVERY.pop(oldest, None)
        _PENDING_RECOVERY[key] = code
    _DRAIN_WAKEUP.set()
    _ensure_drain_thread()


_DRAIN_WAKEUP = threading.Event()
_DRAIN_THREAD: deque[threading.Thread | None] = deque([None], maxlen=1)
_DRAIN_TIMEOUT_MS = 15_000


def _drain_pending_recoveries_once() -> int:
    with _PENDING_LOCK:
        keys = list(_PENDING_RECOVERY)
    for key in keys:
        run_pending_recovery(Path(key), timeout_ms=_DRAIN_TIMEOUT_MS)
    return len(keys)


def _drain_thread_main() -> None:
    while True:
        if not _DRAIN_WAKEUP.wait(timeout=60.0):
            continue
        _DRAIN_WAKEUP.clear()
        try:
            _drain_pending_recoveries_once()
        except Exception:
            continue


def _ensure_drain_thread() -> None:
    current = _DRAIN_THREAD[0]
    if current is not None and current.is_alive():
        return
    with _DRAIN_LOCK:
        current = _DRAIN_THREAD[0]
        if current is not None and current.is_alive():
            return
        thread = threading.Thread(
            target=_drain_thread_main,
            name="ralph-explore-recovery-drain",
            daemon=True,
        )
        _DRAIN_THREAD[0] = thread
        thread.start()


_DRAIN_LOCK = threading.Lock()


def run_pending_recovery(
    workspace_root: Path,
    *,
    timeout_ms: int = 5_000,
) -> dict[str, object]:
    key = _pending_key(workspace_root)
    scheduler = build_scheduler(workspace_root)
    with _PENDING_LOCK:
        code = _PENDING_RECOVERY.get(key)
    if code is None:
        return scheduler.snapshot()
    if code == "no_committed_generation" and _committed_generation_healthy(workspace_root):
        with _PENDING_LOCK:
            _PENDING_RECOVERY.pop(key, None)
        scheduler.mark_healthy()
        scheduler.clear_persisted()
        return scheduler.snapshot()
    if not scheduler.should_attempt_recovery():
        return scheduler.snapshot()
    with _PENDING_LOCK:
        _PENDING_RECOVERY.pop(key, None)
    return run_recovery_action(
        scheduler,
        workspace_root=workspace_root,
        fault_code=code,
        reason="pending_recovery",
        timeout_ms=timeout_ms,
    )


__all__ = [
    "DEFAULT_BACKOFF_BASE_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_BACKOFF_SECONDS",
    "HealthState",
    "RecoveryScheduler",
    "advisory_lock_path",
    "build_scheduler",
    "clear_pending_recovery",
    "clear_persisted_state",
    "enqueue_recovery",
    "pending_recovery_code",
    "read_persisted_state",
    "release_advisory_lock",
    "run_pending_recovery",
    "run_recovery_action",
    "try_advisory_lock",
]


def read_persisted_state(workspace_root: Path) -> dict[str, object] | None:
    state = _read_state_file(workspace_root)
    if state is None:
        return None
    return {
        "health": state.health.value,
        "last_failure_code": state.last_failure_code,
        "last_failure_message": state.last_failure_message,
        "recovery_attempts": state.recovery_attempts,
        "next_recovery_at": state.next_recovery_at,
        "cooldown_remaining": state.cooldown_remaining,
    }


def clear_persisted_state(workspace_root: Path) -> None:
    path = _state_file(workspace_root)
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def pending_recovery_code(workspace_root: Path) -> str | None:
    key = _pending_key(workspace_root)
    with _PENDING_LOCK:
        return _PENDING_RECOVERY.get(key)


def clear_pending_recovery(workspace_root: Path | None = None) -> None:
    with _PENDING_LOCK:
        if workspace_root is None:
            _PENDING_RECOVERY.clear()
        else:
            _PENDING_RECOVERY.pop(_pending_key(workspace_root), None)
