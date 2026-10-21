"""Incremental chain verifier over one wire-ledger file.

The ledger is append-only and grows for the whole life of a run: the
bridge's periodic health probe alone chains three frames every few
seconds, so a long run reaches tens of thousands of rows. Re-reading and
re-HMACing the whole file on every lookup made each completion check
(run once per reader tick) cost O(ledger) -- measured at ~1.3 s for a
56k-row ledger -- so Ralph got steadily slower the longer it ran.

:class:`WireLedgerIndex` verifies each appended byte once. It remembers
how far it has verified, the chain head at that point, and the
``tools/call`` rows seen so far, and on the next lookup only reads what
was appended since. Any sign the verified prefix was rewritten -- a new
inode, a shorter file, a same-size rewrite, or the last verified line no
longer matching on disk -- discards the cache and re-verifies from the
start, so a tampered ledger still fails closed.

Lives in its own module so the repository's one-class-per-file policy
keeps ``_wire_ledger.py`` focused on the append / verify API.
"""

from __future__ import annotations

import hmac
import json
import os
import threading
from typing import TYPE_CHECKING, BinaryIO, cast

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

__all__ = ["GENESIS_HMAC", "WireLedgerIndex", "parse_ledger_line"]

GENESIS_HMAC = "0" * 64


def parse_ledger_line(line: bytes) -> dict[str, object] | None:
    """Return the JSON object on ``line``, or ``None`` for a blank/unparseable line."""
    stripped = line.strip()
    if not stripped:
        return None
    try:
        parsed: object = json.loads(stripped)
    except ValueError:
        return None
    if isinstance(parsed, dict):
        return cast("dict[str, object]", parsed)
    return None


class WireLedgerIndex:
    """Append-aware verified view of one ledger file under one secret.

    ``expected_hmac(row, prior)`` returns the HMAC the appender would
    have sealed ``row`` with given chain head ``prior``, or ``None`` when
    the row is malformed. Thread-safe: concurrent lookups serialize on an
    internal lock.
    """

    def __init__(
        self,
        path: Path,
        expected_hmac: Callable[[dict[str, object], str], str | None],
    ) -> None:
        self._path = path
        self._expected_hmac = expected_hmac
        self._lock = threading.Lock()
        self._identity: tuple[int, int] | None = None
        self._offset = 0
        self._mtime_ns = 0
        self._tail_line = b""
        self._head = GENESIS_HMAC
        self._intact = True
        self._calls: list[dict[str, object]] = []  # bounded-accumulator-ok: one entry per verified tools/call row in this ledger file

    def snapshot(self) -> tuple[bool, tuple[dict[str, object], ...]]:
        """Return ``(chain_intact, tools/call rows)`` for the ledger as it is now.

        An absent or unreadable ledger trivially verifies with no rows,
        matching the full-read verifier this replaces. A trailing line
        with no newline yet (an append in flight) is evaluated but not
        committed, so it is re-read once the writer finishes it.
        """
        with self._lock:
            try:
                data = self._read_unverified()
            except OSError:
                self._reset(None)
                return True, ()
            consumed, newline, partial = data.rpartition(b"\n")
            if newline:
                lines = consumed.split(b"\n")
                for line in lines:
                    self._head, self._intact, call = self._step(line, self._head, self._intact)
                    if call is not None:
                        self._calls.append(call)
                self._offset += len(consumed) + 1
                self._tail_line = lines[-1] + b"\n"
            _, intact, call = self._step(partial, self._head, self._intact)
            calls = tuple(self._calls)
            return intact, (*calls, call) if call is not None else calls

    def _read_unverified(self) -> bytes:
        # filesystem-read-ok: incremental tail read of the MCP wire ledger; FileBackend has no offset-read API
        with self._path.open("rb") as handle:
            stat = os.fstat(handle.fileno())
            identity = (stat.st_dev, stat.st_ino)
            if (
                identity != self._identity
                or stat.st_size < self._offset
                or (stat.st_size == self._offset and stat.st_mtime_ns != self._mtime_ns)
                or not self._tail_still_matches(handle)
            ):
                self._reset(identity)
            self._mtime_ns = stat.st_mtime_ns
            handle.seek(self._offset)
            return handle.read()

    def _tail_still_matches(self, handle: BinaryIO) -> bool:
        if self._offset == 0:
            return True
        handle.seek(self._offset - len(self._tail_line))
        return handle.read(len(self._tail_line)) == self._tail_line

    def _reset(self, identity: tuple[int, int] | None) -> None:
        self._identity = identity
        self._offset = 0
        self._mtime_ns = 0
        self._tail_line = b""
        self._head = GENESIS_HMAC
        self._intact = True
        self._calls = []

    def _step(
        self, line: bytes, head: str, intact: bool
    ) -> tuple[str, bool, dict[str, object] | None]:
        """Advance the chain over one line; return ``(head, intact, tools/call row)``."""
        if not intact:
            return head, False, None
        row = parse_ledger_line(line)
        if row is None:
            return head, True, None
        stored = row.get("hmac")
        if row.get("prior_hmac") != head or not isinstance(stored, str):
            return head, False, None
        expected = self._expected_hmac(row, head)
        if expected is None or not hmac.compare_digest(stored, expected):
            return head, False, None
        return stored, True, row if row.get("method") == "tools/call" else None

