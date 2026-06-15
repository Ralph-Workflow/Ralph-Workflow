"""Subagent output capture protocols and implementations.

A ``SubagentOutputCapture`` reads lines from a subagent's observable output
stream. Implementations are agent-specific; the watchdog receives a capture
implementation via the ``DiscoveryStrategy`` it is constructed with.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class SubagentOutputCapture(Protocol):
    """Reads lines from a single subagent's output stream."""

    def read_lines(self, worker_id: str) -> list[str]:
        """Return new lines from the subagent's output stream.

        Implementations should track their own read position so repeated calls
        return only lines that have not been read before.

        Args:
            worker_id: Identifier for the subagent worker (e.g. a PID or
                worker directory name).

        Returns:
            A list of new text lines. An empty list means no new output.
        """
        ...


class FileSubagentOutputCapture:
    """Capture that reads new lines from a log file on disk.

    Tracks the last read byte offset per worker so only new content is
    returned. Detects truncation/rotation by comparing the stored offset to
    the current file size.

    Args:
        path: Path to the subagent's output log file.
        encoding: Text encoding; defaults to utf-8 with surrogateescape so
            partially-written bytes do not crash the reader.
    """

    def __init__(self, path: str, *, encoding: str = "utf-8") -> None:
        self._path = Path(path)
        self._encoding = encoding
        self._position = 0

    def read_lines(self, worker_id: str) -> list[str]:
        """Return new lines from the log file since the last read."""
        try:
            size = self._path.stat().st_size
        except OSError:
            return []

        if size < self._position:
            # Log was truncated/rotated; start from the beginning.
            self._position = 0

        try:
            with self._path.open(encoding=self._encoding, errors="surrogateescape") as fh:
                fh.seek(self._position)
                data = fh.read()
                self._position = fh.tell()
        except OSError:
            return []

        if not data:
            return []

        # Preserve trailing partial line for the next poll.
        if not data.endswith("\n"):
            last_nl = data.rfind("\n")
            encoded_tail = data.encode(self._encoding, errors="surrogateescape")
            if last_nl == -1:
                self._position -= len(encoded_tail)
                return []
            encoded_partial = data[last_nl + 1 :].encode(self._encoding, errors="surrogateescape")
            self._position -= len(encoded_partial)
            data = data[: last_nl + 1]

        return [line for line in data.splitlines() if line]
