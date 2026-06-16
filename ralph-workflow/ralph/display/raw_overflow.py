"""Per-unit raw NDJSON overflow log writer."""

from __future__ import annotations

import re
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")
DEFAULT_MAX_OVERFLOW_FILE_BYTES = 50 * 1024 * 1024


def _sanitize_unit_id(unit_id: str) -> str:
    return _SAFE_CHARS.sub("_", unit_id)


class RawOverflowLog:
    """Append-mode raw log for a single work unit.

    Thread-safe. Silently no-ops on filesystem errors so the display path
    never crashes due to a read-only workspace.
    """

    def __init__(
        self,
        workspace_root: Path,
        unit_id: str,
        *,
        max_bytes: int = DEFAULT_MAX_OVERFLOW_FILE_BYTES,
    ) -> None:
        safe_id = _sanitize_unit_id(unit_id)
        self.path = workspace_root / ".agent" / "raw" / f"{safe_id}.log"
        self._lock = threading.Lock()
        self._first_write = True
        self._disabled = False
        self._max_bytes = max(max_bytes, 0)
        self._bytes_written = 0

    def disable(self) -> None:
        """Permanently disable this log so future appends are no-ops."""
        with self._lock:
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
                    self._disabled = True
                    return False
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self._first_write:
                    self.path.write_bytes(encoded)
                    self._first_write = False
                else:
                    with self.path.open("ab") as fh:
                        fh.write(encoded)
                self._bytes_written += len(encoded)
                return True
            except (OSError, PermissionError):
                self._disabled = True
                return False

    def relative_reference(self, workspace_root: Path) -> str:
        """Return POSIX path relative to *workspace_root*, or absolute on error."""
        try:
            return self.path.relative_to(workspace_root).as_posix()
        except ValueError:
            return self.path.as_posix()

    @property
    def size_bytes(self) -> int:
        """Current on-disk size of the overflow log file in bytes.

        Returns 0 when the file does not exist yet, has been deleted by a
        different process, or is inaccessible due to an OS error. The
        property is a probe — it never raises — so the log-growth
        corroborator can read it without coordination with the append()
        lock.
        """
        if self._disabled:
            return self._bytes_written
        if self._first_write:
            return 0
        if self._bytes_written == 0:
            return 0
        try:
            self.path.stat()
            return self._bytes_written
        except (OSError, PermissionError):
            return 0

    @property
    def is_disabled(self) -> bool:
        """True when the log has been permanently disabled (byte cap reached or I/O error)."""
        return self._disabled


__all__ = ["DEFAULT_MAX_OVERFLOW_FILE_BYTES", "RawOverflowLog"]
