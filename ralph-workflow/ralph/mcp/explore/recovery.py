"""Recovery scheduler for the indexed exploration substrate (S-4).

Every applicable F1-F20 failure mode is mapped to a recovery action.
The scheduler drives the recovery with bounded exponential backoff and
a cross-process advisory lock so concurrent Ralph sessions on one
workspace never trigger duplicate rebuilds.

The scheduler is intentionally minimal: it owns the *decision* to
start, continue, defer, or mark unhealthy and records each detection
in a persistent on-disk state file so the
:class:`ralph.mcp.explore._handlers_index_status` handler can report
the truthful health/last_failure/recovery_attempts/next_recovery_at
payload across sessions and processes. The scheduler ALSO drives
actual recovery: :func:`run_recovery_action` walks the recovered
state, calls the right write path, and the same scheduler can be
queried for the post-recovery health snapshot.

The state machine is:

* ``idle`` -- nothing to do
* ``scheduled`` -- a recovery attempt is queued
* ``running`` -- a writer owns the rebuild
* ``cooldown`` -- exponential backoff after a failure
* ``unhealthy`` -- bounded attempts exhausted; reads still serve
  from live search

The scheduler integrates with the existing :class:`ReindexWriter.claim`
path so concurrent calls coalesce. The cross-process lock is a
``fcntl`` advisory file lock under ``.agent/ralph-explore/.lock`` so
two Ralph processes cannot both rebuild the same index at once.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ralph.mcp.explore._recovery_types import HealthState, _RecoveryState

logger = logging.getLogger(__name__)


# Bounded defaults. The values are immutable per the absolute-budget
# invariants in ``ralph/verify.py`` -- the recovery scheduler must
# not consume more than the documented share of CPU/IO budget.
DEFAULT_BACKOFF_BASE_SECONDS: float = 0.5
DEFAULT_MAX_BACKOFF_SECONDS: float = 8.0
DEFAULT_MAX_ATTEMPTS: int = 4


#: File name for the persisted scheduler state under
#: ``.agent/ralph-explore/``. Each mark_* / record_failure call writes
#: the snapshot so the status handler can report the truthful
#: health / last_failure / recovery_attempts across processes.
_RECOVERY_STATE_FILENAME: str = "recovery_state.json"


def _state_file(workspace_root: Path) -> Path:
    """Return the on-disk path of the persisted scheduler state file."""
    return Path(workspace_root) / ".agent" / "ralph-explore" / _RECOVERY_STATE_FILENAME


def _read_state_file(workspace_root: Path) -> _RecoveryState | None:
    """Return the persisted state for ``workspace_root`` (or ``None``)."""
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
    """Persist ``state`` to the on-disk recovery state file.

    Writes are best-effort: a read-only index directory or any
    filesystem error is non-fatal because the in-process state is
    still authoritative for the current session and the status
    handler will rebuild the file on the next successful write.
    """
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
        # filesystem-write-ok: transient JSON state write, replaced atomically via temp+rename so a SIGKILL mid-write cannot leave a half-written file.
        tmp_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        # filesystem-write-ok: atomic-replace boundary that promotes the temp file in a single syscall.
        tmp_path.replace(path)
    except OSError:
        # Best-effort; the in-process state is still authoritative.
        return


@dataclass
class RecoveryScheduler:
    """Per-workspace recovery scheduler.

    The scheduler owns the bounded backoff and the in-process
    coalescing state. Each state mutation is also persisted to
    ``.agent/ralph-explore/recovery_state.json`` so the
    :class:`ralph.mcp.explore._handlers_index_status` handler can
    report the truthful health/last_failure/recovery_attempts across
    processes. Cross-process coalescing is delegated to the
    :class:`ReindexWriter` lock plus the advisory file lock.
    """

    workspace_root: Path
    initial_attempts: int = 0
    initial_health: HealthState = HealthState.HEALTHY
    backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS
    max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _state: _RecoveryState = field(default_factory=_RecoveryState)
    # When ``True`` the scheduler reloads any persisted state file
    # on construction so the in-process scheduler reflects the
    # latest persisted truth (e.g. a fault recorded by a sibling
    # process). The default keeps the test seam transparent.
    _load_persisted: bool = True

    def __post_init__(self) -> None:
        if self._load_persisted:
            persisted = _read_state_file(self.workspace_root)
            if persisted is not None:
                self._state = persisted

    def snapshot(self) -> dict[str, object]:
        """Return the canonical state payload for ``ralph_index_status``."""
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
        """Record a failed recovery attempt and schedule the next backoff.

        Uses exponential backoff capped at ``max_backoff_seconds`` and
        marks the scheduler ``unhealthy`` once ``recovery_attempts``
        exceeds ``max_attempts``.
        """
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
        """F10: index can be read but not written. Serve from index while fresh."""
        with self._lock:
            self._state.health = HealthState.DEGRADED
        self._persist()

    def mark_stale(self) -> None:
        """R4: stale past threshold; recovery scheduled."""
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
        """Record a detected fault without counting a recovery attempt.

        Query paths call this so ``ralph_index_status`` shows the
        fault immediately. Attempt accounting stays in
        :meth:`record_failure` so a burst of reads cannot exhaust
        the backoff budget.
        """
        with self._lock:
            self._state.health = health
            self._state.last_failure_code = code
            self._state.last_failure_message = message
        self._persist()

    def should_attempt_recovery(
        self, *, clock: Callable[[], float] = time.monotonic
    ) -> bool:
        """Return True when the scheduler should run another recovery attempt.

        Bounded by ``max_attempts`` and the exponential backoff. When
        ``recovery_attempts`` exceeds ``max_attempts``, the scheduler
        is ``unhealthy`` and returns False.
        """
        with self._lock:
            if self._state.recovery_attempts > self.max_attempts:
                return False
            if self._state.next_recovery_at is None:
                return True
            return clock() >= self._state.next_recovery_at

    def _persist(self) -> None:
        """Write the current state to the on-disk recovery state file."""
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
        """Remove the on-disk recovery state file (used at successful recovery)."""
        path = _state_file(self.workspace_root)
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass


_LAST_ADVISORY_LOCK_FD: list[int | None] = [None]  # bounded-accumulator-ok: single-element lock fd cell


def advisory_lock_path(workspace_root: Path) -> Path:
    """Return the path to the cross-process advisory lock file."""
    return Path(workspace_root) / ".agent" / "ralph-explore" / ".recovery.lock"


def try_advisory_lock(workspace_root: Path) -> bool:
    """Attempt to acquire a non-blocking cross-process advisory lock.

    Returns True if the lock was acquired (caller owns it and MUST
    release with :func:`release_advisory_lock`). Returns False when
    another process already holds it -- the caller should defer the
    rebuild rather than start a duplicate writer.

    Uses ``fcntl`` on POSIX and is a no-op on platforms without
    ``fcntl``; the in-process coalescing via :class:`ReindexWriter`
    still applies on those platforms.
    """
    lock_path = advisory_lock_path(workspace_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # resource-lifecycle-ok: cross-process advisory lock requires raw fd;
    # the lock is released by ``release_advisory_lock`` and closed via
    # ``os.close`` so it cannot leak across long-running sessions.
    # filesystem-write-ok: cross-process advisory lock file opened for flock fd
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)  # resource-lifecycle-ok: cross-process advisory lock — released by release_advisory_lock
    try:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            os.close(fd)
            return False
    except ImportError:
        # No fcntl: rely on the in-process coalescing seam only.
        pass
    # Stash the fd on the path's stat so :func:`release_advisory_lock`
    # can find it. We deliberately do NOT remove the file on release;
    # the next call reopens it. The fd is leaked by intent across
    # the in-process scheduler so the OS keeps the lock for the
    # lifetime of the writer. ``release_advisory_lock`` is called at
    # end-of-recovery.
    import contextlib

    with contextlib.suppress(OSError):
        lock_path.touch()
    with contextlib.suppress(OSError):
        lock_path.stat()
    # Store the fd in a module-level cell so release_advisory_lock can find it.
    _LAST_ADVISORY_LOCK_FD[0] = fd
    return True


def release_advisory_lock() -> None:
    """Release the advisory lock acquired by :func:`try_advisory_lock`."""
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
    """Construct a per-workspace scheduler seeded with default state.

    The scheduler reloads any persisted recovery state file under
    ``.agent/ralph-explore/recovery_state.json`` so the returned
    scheduler reflects the latest fault recorded by a sibling
    process. The status handler uses the same seam so the
    ``recovery`` / ``health`` payload stays truthful across sessions.
    """
    return RecoveryScheduler(workspace_root=workspace_root)


def run_recovery_action(
    scheduler: RecoveryScheduler,
    *,
    workspace_root: Path,
    fault_code: str,
    reason: str = "",
    timeout_ms: int = 5_000,
) -> dict[str, object]:
    """Execute the recovery action associated with ``fault_code``.

    The driver wires the scheduler decision to the actual write path:
    F1, F2, F4, F5 schedule a full rebuild via :func:`ralph.mcp.explore.pipeline.reindex`;
    F3 / F11 / F12 / F13 / F14 schedule a changed-files refresh when
    the persisted store has a committed generation, falling back to a
    full rebuild when no committed generation exists; F6 / F20 wipe
    the persisted index and rebuild; F9 / F10 record the backoff
    only; F7 / F18 defer to the next ``should_attempt_recovery``
    gate; F17 / F19 mark the scheduler unhealthy and let the next
    read path serve from live search.

    The driver returns the post-recovery scheduler snapshot so
    callers can chain it into a status payload without a second
    scheduler construction. The cross-process advisory lock
    guarantees only one rebuild runs at a time across sibling
    sessions on the same workspace.
    """
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
            # Another session is rebuilding; the scheduler defers
            # the rebuild to the holder and the next read still
            # falls through to live search.
            scheduler.record_failure(code=code, message="lock_held_by_other_session")
            return scheduler.snapshot()

        if code in {"index_unwritable", "index_read_only"}:
            # F9 / F10: do not attempt writes. The scheduler records
            # the failure and live search continues to serve. After
            # three consecutive failures the scheduler flips to
            # ``unhealthy`` and the next read still falls through
            # to live search, never a failed tool call.
            scheduler.record_failure(code=code, message=reason)
            return scheduler.snapshot()

        if code in {"interrupted_build", "workspace_moved", "version_mismatch", "index_corrupt"}:
            # F4, F5, F6, F20: wipe the existing index and rebuild.
            import shutil

            try:
                if index_dir.exists():
                    # filesystem-write-ok: recovery transient wipe; the old index is rebuilt in the same recovery call.
                    shutil.rmtree(index_dir, ignore_errors=True)
            except OSError:
                pass

        store = ExploreStore(index_dir)
        try:
            # The recovery driver must always end with a fresh
            # committed generation so the (d) acceptance (a later
            # query is served from the index) holds. ``changed``
            # mode is only safe when a committed generation
            # already exists; otherwise we fall back to ``full``
            # mode which builds and commits a generation.
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
            # ``skipped_no_changes`` is a successful no-op (the
            # index already matches the workspace) and must NOT
            # count as a failure; only true error / cancel / timeout
            # paths flip the scheduler to stale.
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
        except Exception as exc:  # indexer error (F18)
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


# One pending recovery code per workspace. Drained by
# ``run_pending_recovery``. Insert refuses growth past this cap so a
# long-lived server cannot accumulate unbounded path keys.
_PENDING_RECOVERY_CAP: int = 64
_PENDING_RECOVERY: dict[str, str] = {}  # bounded-accumulator-ok: capped at _PENDING_RECOVERY_CAP; drained by run_pending_recovery
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
    """Return True when a healthy committed generation already exists.

    Side-effect free disk probe: a ``no_committed_generation`` fault
    queued in one process can outlive the rebuild another process
    completed. Draining such a stale entry must heal the scheduler
    (mark healthy, clear the persisted state) instead of rebuilding
    a healthy index.
    """
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
    keys = tuple(latest.keys())  # sqlite3.Row: explicit keys() view
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
    """Persist a detected fault and queue one recovery for ``code``.

    Query handlers call this on fall-through so status is truthful
    before the response returns. The rebuild itself runs when
    :func:`run_pending_recovery` drains the queue (a background
    daemon thread started by this call drains it, and tests drain
    it synchronously to prove a later query is indexed).
    ``index_locked`` is intentionally absent: another writer already
    owns the rebuild (F7).
    """
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


# Background drain: one daemon thread per process wakes on the
# enqueue event and drains the pending-recovery queue. It is the
# S-2 production drain path -- without it the queue fills and no
# rebuild ever runs (E1 root cause).
_DRAIN_WAKEUP = threading.Event()
_DRAIN_THREAD: list[threading.Thread | None] = [None]  # bounded-accumulator-ok: single-element thread cell
_DRAIN_TIMEOUT_MS = 15_000


def _drain_pending_recoveries_once() -> int:
    """Drain every queued recovery once; return the number attempted."""
    with _PENDING_LOCK:
        keys = list(_PENDING_RECOVERY)
    for key in keys:
        run_pending_recovery(Path(key), timeout_ms=_DRAIN_TIMEOUT_MS)
    return len(keys)


def _drain_thread_main() -> None:
    while _DRAIN_WAKEUP.wait():  # mcp-timeout-ok: daemon drain thread parks on the enqueue event forever by design
        _DRAIN_WAKEUP.clear()
        try:
            _drain_pending_recoveries_once()
        except Exception:
            # Fail-open: the drain thread must never die; the next
            # enqueue re-wakes it and backoff bounds the retries.
            continue


def _ensure_drain_thread() -> None:
    """Start the single background drain thread lazily (idempotent)."""
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
    """Run the queued recovery for ``workspace_root``, if any."""
    key = _pending_key(workspace_root)
    scheduler = build_scheduler(workspace_root)
    with _PENDING_LOCK:
        code = _PENDING_RECOVERY.get(key)
    if code is None:
        return scheduler.snapshot()
    # Heal a wedge: a ``no_committed_generation`` fault queued in one
    # process can outlive the rebuild a sibling process completed.
    # When a healthy committed generation already exists, the queued
    # fault is stale -- mark healthy, drop the queue entry and the
    # persisted scheduler state instead of rebuilding again. Scope-
    # freshness faults (``index_stale_scope`` etc.) still refresh.
    if code == "no_committed_generation" and _committed_generation_healthy(workspace_root):
        with _PENDING_LOCK:
            _PENDING_RECOVERY.pop(key, None)
        scheduler.mark_healthy()
        scheduler.clear_persisted()
        return scheduler.snapshot()
    # Single chokepoint for retry-storm prevention: a drain inside
    # the backoff window re-queues the code and defers the rebuild;
    # the scheduler (max_attempts + exponential backoff) bounds
    # total attempts.
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
    """Return the persisted scheduler state as a public read seam.

    Returns ``None`` when no state file exists or the file is
    unreadable. Used by tests and the status handler to report
    health across processes.
    """
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
    """Remove the on-disk recovery state file (test seam)."""
    path = _state_file(workspace_root)
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def pending_recovery_code(workspace_root: Path) -> str | None:
    """Return the queued recovery fault code for ``workspace_root`` (test seam)."""
    key = _pending_key(workspace_root)
    with _PENDING_LOCK:
        return _PENDING_RECOVERY.get(key)


def clear_pending_recovery(workspace_root: Path | None = None) -> None:
    """Clear queued recovery codes (test seam)."""
    with _PENDING_LOCK:
        if workspace_root is None:
            _PENDING_RECOVERY.clear()
        else:
            _PENDING_RECOVERY.pop(_pending_key(workspace_root), None)
