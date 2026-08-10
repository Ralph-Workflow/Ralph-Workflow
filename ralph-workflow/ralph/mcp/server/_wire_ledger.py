"""The MCP server's wire ledger — the WIRE provenance witness (F2).

Ralph already owns the server process; ``lifecycle.py`` opens a raw
subprocess stdout/stderr log (``mcp-server.log``) for diagnostics. This
module promotes that idea to a structured record: every ``tools/call``
JSON-RPC frame dispatched through :class:`ralph.mcp.server._mcp_server.McpServer`
appends one HMAC-chained JSONL record to a sibling file,
``.agent/tmp/mcp-wire-ledger.jsonl``. The raw diagnostic log is left
untouched — this is a separate, structured file so parsing the ledger never
depends on subprocess stdout buffering.

``Provenance.WIRE`` means *and only means* a matching, HMAC-verifiable
``tools/call`` record exists in this ledger. An unsigned server (no
``RALPH_BROKER_SECRET``) cannot produce one: :func:`append_wire_record`
returns ``None`` when ``secret`` is ``None``, so an unchained ledger is
never mistaken for a ledger (A5).
"""

from __future__ import annotations

import contextlib
import datetime
import fcntl
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.server._wire_ledger_capture import WireLedgerCapture

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "WIRE_LEDGER_RELPATH",
    "WireLedgerCapture",
    "WireLedgerRecord",
    "append_wire_record",
    "collect_captures",
    "render_capture_table_markdown",
    "verify_chain",
    "wire_evidence_for",
]

#: Workspace-relative path to the structured wire ledger. Sibling to
#: ``mcp-server.log`` (the raw subprocess diagnostic stream), which is left
#: untouched by this module.
WIRE_LEDGER_RELPATH = Path(".agent/tmp/mcp-wire-ledger.jsonl")

_GENESIS_HMAC = "0" * 64


@dataclass(frozen=True)
class WireLedgerRecord:
    """One HMAC-chained wire-ledger row for a dispatched JSON-RPC frame."""

    method: str
    tool_name: str | None
    params_digest: str
    run_id: str
    timestamp: float
    prior_hmac: str
    record_hmac: str

    def to_json_line(self) -> str:
        payload: dict[str, object] = {
            "method": self.method,
            "tool_name": self.tool_name,
            "params_digest": self.params_digest,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "prior_hmac": self.prior_hmac,
            "hmac": self.record_hmac,
        }
        return json.dumps(payload, sort_keys=True)


def _params_digest(params: dict[str, object]) -> str:
    canonical = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _record_hmac(
    secret: str,
    *,
    method: str,
    tool_name: str | None,
    params_digest: str,
    run_id: str,
    timestamp: float,
    prior_hmac: str,
) -> str:
    """Compute the HMAC-SHA256 binding one ledger record to ``secret``.

    The chain links each record to its predecessor's HMAC (``prior_hmac``),
    mirroring the ``hmac.new(secret.encode(), msg, hashlib.sha256)`` pattern
    already used by ``completion_signals.py`` / ``completion_receipts.py`` /
    ``coordination.py``. A row that is inserted, deleted, or reordered
    breaks the chain for every subsequent record, so :func:`verify_chain`
    detects tampering anywhere in the file, not just at the tampered row.
    """
    msg = f"{method}\n{tool_name or ''}\n{params_digest}\n{run_id}\n{timestamp!r}\n{prior_hmac}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def _iter_ledger_rows(ledger_path: Path) -> list[dict[str, object]]:
    try:
        raw = ledger_path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[dict[str, object]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed: object = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(cast("dict[str, object]", parsed))
    return rows


def collect_captures(
    workspace_root: Path, secret: str | None
) -> list[WireLedgerCapture]:
    """Return every verified capture row in ``workspace_root``'s wire ledger.

    S-9 / F5: the wire-ledger-backed view of every frame a real AGY
    run dispatched through the MCP server. Only HMAC-verifiable rows
    are returned -- an unverifiable ledger backs nothing, so its
    captures cannot be promoted to the fixture table either.

    A missing or empty ledger returns an empty list (no captures yet,
    no captures to report). This is the live-only source of truth the
    ``render_capture_table_markdown`` exporter consumes.
    """
    if secret is None:
        return []
    if not verify_chain(workspace_root, secret):
        return []
    ledger_path = workspace_root / WIRE_LEDGER_RELPATH
    captures: list[WireLedgerCapture] = []
    for row in _iter_ledger_rows(ledger_path):
        capture = WireLedgerCapture.from_row(row)
        if capture is not None:
            captures.append(capture)
    return captures


def render_capture_table_markdown(
    captures: list[WireLedgerCapture], *, run_id: str | None = None
) -> str:
    """Render ``captures`` as a markdown capture-method table for the AGY wire-provenance fixture.

    S-9 / F5: a live run deposits replayable frames into the wire
    ledger; this exporter turns those frames into the same capture-
    method table that ``tests/display/_fixtures/agy_wire_provenance.md``
    previously documented by hand. The output is deterministic for a
    given ordered input (rows are emitted in input order -- the ledger
    is HMAC-chained so insertion order is the canonical order).

    When ``run_id`` is given, only captures whose ``run_id`` matches
    are included (multi-run ledgers do not bleed into each other's
    capture tables).

    The table's columns are: method, tool_name, run_id, captured-at
    (ISO-8601 UTC). ``captured-at`` is sourced from the row's HMAC-
    sealed timestamp -- an unverifiable row never reaches this table,
    so the timestamp itself is durable.
    """
    lines: list[str] = []
    lines.append("| method | tool_name | run_id | captured-at (UTC) |")
    lines.append("| --- | --- | --- | --- |")
    for capture in captures:
        if run_id is not None and capture.run_id != run_id:
            continue
        # ISO-8601 UTC second precision; the ledger stores float seconds.
        captured_at = (
            datetime.datetime.fromtimestamp(capture.timestamp, tz=datetime.UTC)
            .replace(microsecond=0)
            .isoformat()
        )
        lines.append(
            f"| {capture.method} | {capture.tool_name or ''} | "
            f"{capture.run_id} | {captured_at} |"
        )
    return "\n".join(lines) + "\n"


def _read_last_hmac(ledger_path: Path) -> str:
    rows = _iter_ledger_rows(ledger_path)
    if not rows:
        return _GENESIS_HMAC
    stored = rows[-1].get("hmac")
    return stored if isinstance(stored, str) else _GENESIS_HMAC


def append_wire_record(
    workspace_root: Path,
    *,
    method: str,
    tool_name: str | None,
    params: dict[str, object],
    run_id: str,
    secret: str | None,
    clock: Callable[[], float] = time.time,
) -> WireLedgerRecord | None:
    """Append one HMAC-chained record for a dispatched JSON-RPC frame.

    Returns ``None`` (no record appended, nothing written) when ``secret``
    is ``None``: an unsigned server cannot produce a WIRE-grade witness —
    "an unchained ledger is not a ledger" (F2 / A5).

    Two concurrent MCP server instances writing to the same
    ``WIRE_LEDGER_RELPATH`` (the restart-aware bridge tears down a
    previous server and starts a new one in the same turn, and the
    smoke harness drives a sequence of restart-aware turns) would
    otherwise each read the same prior_hmac, each compute a new
    record, and each append — leaving the on-disk chain with two
    children of the same parent. ``verify_chain`` then fails at the
    first non-contiguous row, so even a fully-correct run never grades
    ``WIRE``. Acquire an exclusive ``fcntl.flock`` on a sidecar lock
    file and hold it across the read + write so every append is
    serialized. The lock file lives next to the ledger; it is created
    on first use and never removed (its presence is a feature, not
    garbage). The lock is non-blocking best-effort: a lock that cannot
    be acquired is treated as a no-op append for that frame, since
    dropping a single tool-call witness is preferable to corrupting
    the chain (the dropped frame never grades ``WIRE``, but the
    surviving frames in the same run still can).
    """
    if secret is None:
        return None
    ledger_path = workspace_root / WIRE_LEDGER_RELPATH
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = workspace_root / (str(WIRE_LEDGER_RELPATH) + ".lock")
    # filesystem-write-ok: lock-file sidecar used only to coordinate concurrent appends; the file's contents are never read back, so write_text_if_changed's whole-file replace contract does not fit (it would clobber any in-flight fcntl owner).
    lock_handle = lock_path.open("w", encoding="utf-8")
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            # Another writer holds the lock; the racing frame never
            # grades WIRE but we MUST NOT corrupt the chain by writing
            # without reading the current prior_hmac.
            return None
        prior_hmac = _read_last_hmac(ledger_path)
        timestamp = clock()
        digest = _params_digest(params)
        record_hmac = _record_hmac(
            secret,
            method=method,
            tool_name=tool_name,
            params_digest=digest,
            run_id=run_id,
            timestamp=timestamp,
            prior_hmac=prior_hmac,
        )
        record = WireLedgerRecord(
            method=method,
            tool_name=tool_name,
            params_digest=digest,
            run_id=run_id,
            timestamp=timestamp,
            prior_hmac=prior_hmac,
            record_hmac=record_hmac,
        )
        # filesystem-write-ok: deliberate append-only HMAC-chained JSONL log; each record must append after reading the prior record's hmac, so write_text_if_changed's whole-file replace semantics do not fit.
        with ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(record.to_json_line() + "\n")
    finally:
        with contextlib.suppress(OSError, ValueError):
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()
    return record


def verify_chain(workspace_root: Path, secret: str) -> bool:
    """Return ``True`` iff every record in the ledger HMAC-chains correctly.

    An empty or absent ledger trivially verifies (there is nothing to
    break). Any row whose ``prior_hmac`` does not match its predecessor's
    ``hmac``, or whose own ``hmac`` does not match ``secret``, fails the
    whole chain — a forged or unchained row is never accepted piecemeal.
    """
    ledger_path = workspace_root / WIRE_LEDGER_RELPATH
    rows = _iter_ledger_rows(ledger_path)
    prior = _GENESIS_HMAC
    for row in rows:
        if row.get("prior_hmac") != prior:
            return False
        method = row.get("method")
        params_digest = row.get("params_digest")
        run_id = row.get("run_id")
        timestamp = row.get("timestamp")
        tool_name = row.get("tool_name")
        stored_hmac = row.get("hmac")
        if not (
            isinstance(method, str)
            and isinstance(params_digest, str)
            and isinstance(run_id, str)
            and isinstance(timestamp, int | float)
            and isinstance(stored_hmac, str)
            and (tool_name is None or isinstance(tool_name, str))
        ):
            return False
        expected = _record_hmac(
            secret,
            method=method,
            tool_name=tool_name,
            params_digest=params_digest,
            run_id=run_id,
            timestamp=timestamp,
            prior_hmac=prior,
        )
        if not hmac.compare_digest(stored_hmac, expected):
            return False
        prior = stored_hmac
    return True


def wire_evidence_for(
    workspace_root: Path,
    run_id: str,
    *,
    tool_name: str | None = None,
    secret: str | None,
) -> bool:
    """Return ``True`` iff a verified ``tools/call`` record backs ``run_id``.

    When ``tool_name`` is given, only a record whose ``tool_name`` matches
    (case-insensitive substring match, so a canonical tool name like
    ``ralph_submit_md_artifact`` matches a lookup for ``"artifact"``)
    counts. ``tool_name=None`` matches any ``tools/call`` record for the run
    — the general "did the agent dial the MCP server at all" signal.

    Returns ``False`` (never grades ``WIRE``) when ``secret`` is ``None`` or
    the chain fails to verify — an unverifiable ledger backs nothing.
    """
    if secret is None:
        return False
    if not verify_chain(workspace_root, secret):
        return False
    ledger_path = workspace_root / WIRE_LEDGER_RELPATH
    for row in _iter_ledger_rows(ledger_path):
        if row.get("run_id") != run_id or row.get("method") != "tools/call":
            continue
        if tool_name is None:
            return True
        row_tool = row.get("tool_name")
        if isinstance(row_tool, str) and tool_name.lower() in row_tool.lower():
            return True
    return False
