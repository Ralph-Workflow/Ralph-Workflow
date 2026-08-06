"""Per-unit raw NDJSON overflow log writer."""

from __future__ import annotations

import contextlib
import threading
import time
from typing import TYPE_CHECKING, BinaryIO, cast

from ralph.display.record_writer import safe_id_for

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

DEFAULT_MAX_OVERFLOW_FILE_BYTES = 50 * 1024 * 1024
#: Userspace buffer for the persistent handle. Amortizes write syscalls
#: (and the fsevents they generate) across many appended lines.
_BUFFER_BYTES = 64 * 1024
#: Default seconds between forced flushes. MUST stay well below
#: ralph.timeout_defaults.LOG_GROWTH_SECONDS (30.0): operators tail this
#: file and the on-disk copy must never look wedged while the unit is live.
DEFAULT_FLUSH_INTERVAL_SECONDS = 5.0


#: Shared-by-path registry (S-8 / C4). Two callers constructing
#: ``RawOverflowLog`` for the same path (a reader-owned instance and a
#: display-owned instance, by far the common case) used to receive two
#: independent objects that shared ``self.path`` but neither lock nor
#: ``_first_write`` state -- whichever object's first ``append()`` ran
#: later opened the file in ``"wb"`` mode and truncated the other
#: object's already-written bytes, the plausible source of the
#: measured 2026-08-06 NUL-hole corruption. The registry keys on the
#: resolved path so all callers share one object per path.
_REGISTRY_LOCK = threading.Lock()
_REGISTRY: dict[str, RawOverflowLog] = {}  # bounded-accumulator-ok: per-path singleton, scoped to process lifetime


def get_or_create_raw_overflow_log(
    workspace_root: Path,
    unit_id: str,
    *,
    model: str | None = None,
    max_bytes: int = DEFAULT_MAX_OVERFLOW_FILE_BYTES,
    flush_interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
    now: Callable[[], float] = time.monotonic,
) -> RawOverflowLog:
    """Return the per-path ``RawOverflowLog`` for ``(workspace_root, unit_id, model)``.

    Process-wide per-path singleton: callers constructing
    ``RawOverflowLog`` for the same path receive the same instance so
    the lock and ``_first_write`` state are shared and a late first
    ``append()`` from one caller cannot truncate another caller's
    already-written bytes (S-8 / C4 / DoD 15). Returns the existing
    instance on a repeat call -- not a fresh one.
    """
    key_path = (
        workspace_root / ".agent" / "raw" / f"{safe_id_for(unit_id, model)}.log"
    )
    key = str(key_path.resolve(strict=False))
    with _REGISTRY_LOCK:
        existing = _REGISTRY.get(key)
        if existing is not None:
            return existing
        instance = RawOverflowLog(
            workspace_root,
            unit_id,
            model=model,
            max_bytes=max_bytes,
            flush_interval_seconds=flush_interval_seconds,
            now=now,
        )
        _REGISTRY[key] = instance
        return instance


def _forget_raw_overflow_log(key_path: str) -> None:
    """Drop one entry from the registry. Used by tests; not part of the public API."""
    with _REGISTRY_LOCK:
        _REGISTRY.pop(key_path, None)


class RawOverflowLog:
    """Append-mode raw log for a single work unit.

    Thread-safe. Holds one buffered file handle open for the unit's
    lifetime instead of opening/closing per line (the per-line pattern
    generated an fsevent storm on long runs). Silently no-ops on
    filesystem errors so the display path never crashes due to a
    read-only workspace.
    """

    def __init__(
        self,
        workspace_root: Path,
        unit_id: str,
        *,
        model: str | None = None,
        max_bytes: int = DEFAULT_MAX_OVERFLOW_FILE_BYTES,
        flush_interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        # S-23 (wt-028-display): pair the verbatim capture with the
        # rendered record by deriving the file id from the same
        # ``safe_id_for(agent, model)`` helper. Without the model
        # suffix a mismatched pair (e.g. ``pi.log`` here, ``pi_X.log``
        # there) would orphan the rendered record's condensation
        # markers from the verbatim capture they point at.
        safe_id = safe_id_for(unit_id, model)
        self.path = workspace_root / ".agent" / "raw" / f"{safe_id}.log"
        self._lock = threading.Lock()
        self._first_write = True
        self._disabled = False
        self._max_bytes = max(max_bytes, 0)
        self._bytes_written = 0
        self._flush_interval = max(flush_interval_seconds, 0.0)
        self._now = now
        self._fh: BinaryIO | None = None
        self._last_flush = now()

    def disable(self) -> None:
        """Permanently disable this log so future appends are no-ops."""
        with self._lock:
            self._close_locked()
            self._disabled = True

    def append(self, line: str) -> bool:
        """Write *line* to the overflow log.

        Returns True when the line was written. Returns False when the log is
        disabled, the byte cap has been reached, or an I/O error occurs.
        """
        with self._lock:
            if self._disabled:
                return False
            try:
                text = line.rstrip("\n") + "\n"
                encoded = text.encode("utf-8")
                if self._bytes_written + len(encoded) > self._max_bytes:
                    self._close_locked()
                    self._disabled = True
                    return False
                if self._fh is None:
                    # filesystem-write-ok: bounded binary overflow stream directory creation
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    mode = "wb" if self._first_write else "ab"
                    # filesystem-write-ok: bounded binary overflow stream remains live until byte cap
                    handle_obj: object = self.path.open(mode, buffering=_BUFFER_BYTES)
                    self._fh = cast(
                        "BinaryIO", handle_obj
                    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
                    self._first_write = False
                fh: BinaryIO | None = self._fh
                if fh is None:
                    return False
                fh.write(encoded)
                self._bytes_written += len(encoded)
                if self._now() - self._last_flush >= self._flush_interval:
                    fh.flush()
                    self._last_flush = self._now()
                return True
            except (OSError, PermissionError):
                self._close_locked()
                self._disabled = True
                return False

    def flush(self) -> None:
        """Force buffered bytes to disk. Never raises."""
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.flush()
                    self._last_flush = self._now()
                except (OSError, PermissionError):
                    self._close_locked()
                    self._disabled = True

    def close(self) -> None:
        """Flush and release the file handle. Idempotent; appends may reopen."""
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._fh is not None:
            with contextlib.suppress(OSError, PermissionError):
                self._fh.close()
            self._fh = None

    def relative_reference(self, workspace_root: Path) -> str:
        """Return POSIX path relative to *workspace_root*, or absolute on error."""
        try:
            return self.path.relative_to(workspace_root).as_posix()
        except ValueError:
            return self.path.as_posix()

    @property
    def size_bytes(self) -> int:
        """Bytes appended so far (buffered bytes included).

        The idle watchdog's log-growth corroborator reads this to prove the
        unit is alive; it must advance on every append, not only on flush.
        Returns 0 before the first write. Never raises.

        The in-memory ``_bytes_written`` counter is the authoritative
        liveness signal — an on-disk ``stat()`` probe is intentionally
        avoided because a missing or unfetchable file (operator unlink,
        watcher quarantine, transient I/O error) must NOT silence the
        watchdog while the unit itself is still appending.
        """
        return self._bytes_written

    @property
    def is_disabled(self) -> bool:
        """True when the log has been permanently disabled (byte cap reached or I/O error)."""
        return self._disabled


__all__ = [
    "DEFAULT_FLUSH_INTERVAL_SECONDS",
    "DEFAULT_MAX_OVERFLOW_FILE_BYTES",
    "RawOverflowLog",
]
