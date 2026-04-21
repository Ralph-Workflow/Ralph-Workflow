"""Per-unit raw NDJSON overflow log writer."""

from __future__ import annotations

import re
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_unit_id(unit_id: str) -> str:
    return _SAFE_CHARS.sub("_", unit_id)


class RawOverflowLog:
    """Append-mode raw log for a single work unit.

    Thread-safe. Silently no-ops on filesystem errors so the display path
    never crashes due to a read-only workspace.
    """

    def __init__(self, workspace_root: Path, unit_id: str) -> None:
        safe_id = _sanitize_unit_id(unit_id)
        self.path = workspace_root / ".agent" / "raw" / f"{safe_id}.log"
        self._lock = threading.Lock()
        self._first_write = True
        self._disabled = False

    def disable(self) -> None:
        """Permanently disable this log so future appends are no-ops."""
        with self._lock:
            self._disabled = True

    def append(self, line: str) -> None:
        """Write *line* to the overflow log. No-op on any I/O error."""
        with self._lock:
            if self._disabled:
                return
            try:
                text = line.rstrip("\n") + "\n"
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self._first_write:
                    self.path.write_text(text, encoding="utf-8")
                    self._first_write = False
                else:
                    with self.path.open("a", encoding="utf-8") as fh:
                        fh.write(text)
            except (OSError, PermissionError):
                self._disabled = True

    def relative_reference(self, workspace_root: Path) -> str:
        """Return POSIX path relative to *workspace_root*, or absolute on error."""
        try:
            return self.path.relative_to(workspace_root).as_posix()
        except ValueError:
            return self.path.as_posix()


__all__ = ["RawOverflowLog"]
