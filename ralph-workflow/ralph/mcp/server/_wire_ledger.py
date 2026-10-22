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

S-6 (criterion 17) extends the per-row witness with optional
``delivery_mode``, ``provider``, ``model_id``, and ``agent_id`` fields
so the wire-ledger-backed capture table records the same delivery
metadata the multimodal platform itself reports. The four fields are
OPTIONAL and default to ``None``; when every one is absent the on-disk
row shape and the HMAC message bytes are byte-for-byte identical to
the pre-S-6 shape, so a ledger written by a pre-S-6 server still
verifies under the post-S-6 verifier (backward compatibility).
"""

from __future__ import annotations

import contextlib
import datetime
import fcntl
import hashlib
import hmac
import itertools
import json
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from loguru import logger

from ralph.mcp.server._wire_ledger_capture import WireLedgerCapture
from ralph.mcp.server._wire_ledger_index import (
    GENESIS_HMAC,
    WireLedgerIndex,
    parse_ledger_line,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class WireLedgerIOError(Exception):
    """Raised when the wire ledger cannot append a record due to an I/O error.

    The error message contains the substring ``wire_ledger_io_error`` so consumers
    can branch on it. This makes the ledger fail-closed: an I/O error during
    append is surfaced as a typed error rather than silently swallowed.
    """


__all__ = [
    "WIRE_LEDGER_RELPATH",
    "WireLedgerCapture",
    "WireLedgerIOError",
    "WireLedgerRecord",
    "append_wire_record",
    "collect_captures",
    "params_digest",
    "render_capture_table_markdown",
    "verify_chain",
    "wire_evidence_for",
]

#: Workspace-relative path to the structured wire ledger. Sibling to
#: ``mcp-server.log`` (the raw subprocess diagnostic stream), which is left
#: untouched by this module.
WIRE_LEDGER_RELPATH = Path(".agent/tmp/mcp-wire-ledger.jsonl")

_GENESIS_HMAC = GENESIS_HMAC

#: Bytes read per step when scanning the ledger backwards for its chain
#: head. One row is a few hundred bytes, so one chunk almost always holds it.
TAIL_CHUNK_BYTES = 64 * 1024

#: How long an append waits for the sidecar lock before dropping its frame.
#: Bounded (MCP timeout contract): a writer descheduled while holding the lock
#: on a loaded host must not stall dispatch indefinitely.
DEFAULT_LOCK_TIMEOUT_S: Final[float] = 1.0

_LOCK_POLL_INTERVAL_S: Final[float] = 0.01

#: Log the first dropped witness, then one line per this many, so a sustained
#: contention storm costs a handful of log lines instead of one per frame.
_DROP_LOG_EVERY: Final[int] = 100

#: Frames dropped to lock contention in this process; ``next()`` is atomic.
_dropped_frames = itertools.count(1)

#: Verified views kept per process, keyed by (ledger path, secret
#: fingerprint). A run touches one or two ledgers; the cap only bounds
#: processes (test suites) that verify many short-lived ledgers.
_MAX_CACHED_INDEXES = 32
_INDEXES_LOCK = threading.Lock()
_INDEXES: OrderedDict[tuple[str, str], WireLedgerIndex] = (
    OrderedDict()
)  # bounded-accumulator-ok: LRU-capped at _MAX_CACHED_INDEXES


def _ledger_path(workspace_root: Path, secret: str | None) -> Path:
    if secret is None:
        return workspace_root / WIRE_LEDGER_RELPATH
    fingerprint = hashlib.sha256(secret.encode()).hexdigest()[:32]
    return workspace_root / ".agent" / "tmp" / f"mcp-wire-ledger-{fingerprint}.jsonl"


@dataclass(frozen=True)
class WireLedgerRecord:
    """One HMAC-chained wire-ledger row for a dispatched JSON-RPC frame.

    The four ``delivery_mode`` / ``provider`` / ``model_id`` / ``agent_id``
    fields are optional and default to ``None``. They are S-6 (criterion
    17) additions that let the wire ledger record the same multimodal
    delivery metadata the multimodal platform emits, so the capture
    table can be regenerated deterministically. A pre-S-6 record (or a
    non-multimodal ``tools/call`` row) is constructed with all four
    fields left at their default ``None``; the resulting on-disk row
    and the HMAC message bytes are byte-for-byte identical to the
    pre-S-6 shape, so a legacy ledger still verifies under the
    post-S-6 verifier.
    """

    method: str
    tool_name: str | None
    params_digest: str
    run_id: str
    timestamp: float
    prior_hmac: str
    record_hmac: str
    delivery_mode: str | None = None
    provider: str | None = None
    model_id: str | None = None
    agent_id: str | None = None
    capability_profile_digest: str | None = None

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
        # S-6: only serialize the optional fields when they were set,
        # so a row produced with all four at their default ``None``
        # stays byte-for-byte identical to the pre-S-6 row shape.
        if self.delivery_mode is not None:
            payload["delivery_mode"] = self.delivery_mode
        if self.provider is not None:
            payload["provider"] = self.provider
        if self.model_id is not None:
            payload["model_id"] = self.model_id
        if self.agent_id is not None:
            payload["agent_id"] = self.agent_id
        if self.capability_profile_digest is not None:
            payload["capability_profile_digest"] = self.capability_profile_digest
        return json.dumps(payload, sort_keys=True)


def params_digest(params: dict[str, object]) -> str:
    """Return the canonical SHA-256 digest of ``params`` as a row-stored.

    Public, stable entrypoint for callers that need to recompute the digest a
    ledger row carries (e.g. the multimodal smoke grader matches a verified
    ``tools/call`` record's ``params_digest`` to the digest of
    ``{"path": <replay-handle>}`` so the agent cannot fake the replay hop).
    The on-disk record shape (LM hash algorithm, sort_keys canonical JSON,
    ``default=str`` for unsupported types) is unchanged.
    """
    canonical = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _params_digest(params: dict[str, object]) -> str:
    """Internal alias for :func:`params_digest` preserved for callers."""
    return params_digest(params)


def _record_hmac(
    secret: str,
    *,
    method: str,
    tool_name: str | None,
    params_digest: str,
    run_id: str,
    timestamp: float,
    prior_hmac: str,
    delivery_mode: str | None = None,
    provider: str | None = None,
    model_id: str | None = None,
    agent_id: str | None = None,
    capability_profile_digest: str | None = None,
) -> str:
    """Compute the HMAC-SHA256 binding one ledger record to ``secret``.

    The chain links each record to its predecessor's HMAC (``prior_hmac``),
    mirroring the ``hmac.new(secret.encode(), msg, hashlib.sha256)`` pattern
    already used by ``completion_signals.py`` / ``completion_receipts.py`` /
    ``coordination.py``. A row that is inserted, deleted, or reordered
    breaks the chain for every subsequent record, so :func:`verify_chain`
    detects tampering anywhere in the file, not just at the tampered row.

    S-6 (criterion 17): the four optional ``delivery_mode`` /
    ``provider`` / ``model_id`` / ``agent_id`` fields, when ANY of
    them is present, are concatenated onto the message in a fixed
    order after the base six fields. When ALL four are ``None`` the
    message bytes are byte-for-byte identical to the pre-S-6 message
    so a legacy row still verifies under the post-S-6 verifier.
    """
    msg = f"{method}\n{tool_name or ''}\n{params_digest}\n{run_id}\n{timestamp!r}\n{prior_hmac}".encode()
    if delivery_mode is not None:
        msg += f"\ndelivery_mode={delivery_mode}".encode()
    if provider is not None:
        msg += f"\nprovider={provider}".encode()
    if model_id is not None:
        msg += f"\nmodel_id={model_id}".encode()
    if agent_id is not None:
        msg += f"\nagent_id={agent_id}".encode()
    if capability_profile_digest is not None:
        msg += f"\ncapability_profile_digest={capability_profile_digest}".encode()
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


def collect_captures(workspace_root: Path, secret: str | None) -> list[WireLedgerCapture]:
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
    ledger_path = _ledger_path(workspace_root, secret)
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
            f"| {capture.method} | {capture.tool_name or ''} | {capture.run_id} | {captured_at} |"
        )
    return "\n".join(lines) + "\n"


def _read_last_hmac(ledger_path: Path) -> str:
    """Return the chain head: the ``hmac`` of the ledger's last parseable row.

    Scans backwards from the end of the file so an append costs O(1) in
    the ledger's length. It runs under the append lock, and a full read
    there made every frame of a long run slower than the last until
    appends overran the 0.2 s lock deadline.
    """
    try:
        # filesystem-read-ok: backwards tail scan of the MCP wire ledger under its append lock; FileBackend has no offset-read API
        with ledger_path.open("rb") as handle:
            end = handle.seek(0, os.SEEK_END)
            carry = b""
            while end > 0:
                start = max(0, end - TAIL_CHUNK_BYTES)
                handle.seek(start)
                lines = (handle.read(end - start) + carry).split(b"\n")
                end = start
                # Unless the scan reached the start of the file, the first
                # piece may be the tail of a line that began further back.
                carry = lines.pop(0) if end > 0 else b""
                for line in reversed(lines):
                    row = parse_ledger_line(line)
                    if row is not None:
                        stored = row.get("hmac")
                        return stored if isinstance(stored, str) else _GENESIS_HMAC
    except OSError:
        return _GENESIS_HMAC
    return _GENESIS_HMAC


def _note_dropped_frame(method: str) -> None:
    """Record one frame dropped to lock contention, logging at a bounded rate."""
    dropped = next(_dropped_frames)
    if dropped == 1 or dropped % _DROP_LOG_EVERY == 0:
        logger.warning(
            "wire ledger: lock held past the deadline, witness dropped for {} "
            "({} dropped in this process); the chain stays intact",
            method,
            dropped,
        )


def append_wire_record(
    workspace_root: Path,
    *,
    method: str,
    tool_name: str | None,
    params: dict[str, object],
    run_id: str,
    secret: str | None,
    clock: Callable[[], float] = time.time,
    delivery_mode: str | None = None,
    provider: str | None = None,
    model_id: str | None = None,
    agent_id: str | None = None,
    capability_profile_digest: str | None = None,
    lock_timeout_s: float | None = None,
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

    S-6 (criterion 17): the four optional ``delivery_mode`` /
    ``provider`` / ``model_id`` / ``agent_id`` kwargs are forwarded
    to the HMAC and to the on-disk row. When every one is ``None`` the
    on-disk row and HMAC match the pre-S-6 shape byte-for-byte, so
    pre-S-6 ledgers still verify under the post-S-6 verifier.
    """
    if secret is None:
        return None
    try:
        ledger_path = _ledger_path(workspace_root, secret)
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = ledger_path.with_name(ledger_path.name + ".lock")
        # filesystem-write-ok: lock-file sidecar used only to coordinate concurrent appends; the file's contents are never read back, so write_text_if_changed's whole-file replace contract does not fit (it would clobber any in-flight fcntl owner).
        lock_handle = lock_path.open("w", encoding="utf-8")
    except OSError as e:
        raise WireLedgerIOError(f"wire_ledger_io_error: failed setup for wire ledger: {e}") from e

    try:
        deadline = time.monotonic() + (
            DEFAULT_LOCK_TIMEOUT_S if lock_timeout_s is None else lock_timeout_s
        )
        while True:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    _note_dropped_frame(method)
                    return None
                # filesystem-poll-ok: bounded retry loop for wire ledger flock acquisition
                time.sleep(_LOCK_POLL_INTERVAL_S)
            except OSError as e:
                raise WireLedgerIOError(
                    f"wire_ledger_io_error: lock contention or lock failure: {e}"
                ) from e
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
            delivery_mode=delivery_mode,
            provider=provider,
            model_id=model_id,
            agent_id=agent_id,
            capability_profile_digest=capability_profile_digest,
        )
        record = WireLedgerRecord(
            method=method,
            tool_name=tool_name,
            params_digest=digest,
            run_id=run_id,
            timestamp=timestamp,
            prior_hmac=prior_hmac,
            record_hmac=record_hmac,
            delivery_mode=delivery_mode,
            provider=provider,
            model_id=model_id,
            agent_id=agent_id,
            capability_profile_digest=capability_profile_digest,
        )
        try:
            # filesystem-write-ok: deliberate append-only HMAC-chained JSONL log; each record must append after reading the prior record's hmac, so write_text_if_changed's whole-file replace semantics do not fit.
            with ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(record.to_json_line() + "\n")
        except OSError as e:
            raise WireLedgerIOError(
                f"wire_ledger_io_error: failed to append wire ledger record: {e}"
            ) from e
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

    Verification is incremental (:class:`WireLedgerIndex`): each appended
    row is HMAC-checked once per process, and a rewrite of the verified
    prefix forces a full re-verify.
    """
    intact, _calls = _ledger_index(workspace_root, secret).snapshot()
    return intact


def _ledger_index(workspace_root: Path, secret: str) -> WireLedgerIndex:
    ledger_path = _ledger_path(workspace_root, secret)
    key = (str(ledger_path), hashlib.sha256(secret.encode()).hexdigest())
    with _INDEXES_LOCK:
        index = _INDEXES.get(key)
        if index is None:
            index = WireLedgerIndex(
                ledger_path, lambda row, prior: expected_row_hmac(secret, row, prior)
            )
            _INDEXES[key] = index
            while len(_INDEXES) > _MAX_CACHED_INDEXES:
                _INDEXES.popitem(last=False)
        else:
            _INDEXES.move_to_end(key)
        return index


def expected_row_hmac(secret: str, row: dict[str, object], prior: str) -> str | None:
    """Recompute the HMAC ``row`` was sealed with, or ``None`` if it is malformed.

    S-6 (criterion 17): the four optional S-6 fields are read from the
    row (defaulting to ``None`` when absent -- a pre-S-6 row shape)
    and forwarded to :func:`_record_hmac` so the recomputed HMAC
    matches the bytes the appender signed. A pre-S-6 row therefore
    still verifies under the post-S-6 verifier.
    """
    method = row.get("method")
    params_digest = row.get("params_digest")
    run_id = row.get("run_id")
    timestamp = row.get("timestamp")
    tool_name = row.get("tool_name")
    if not (
        isinstance(method, str)
        and isinstance(params_digest, str)
        and isinstance(run_id, str)
        and isinstance(timestamp, int | float)
        and (tool_name is None or isinstance(tool_name, str))
    ):
        return None
    return _record_hmac(
        secret,
        method=method,
        tool_name=tool_name,
        params_digest=params_digest,
        run_id=run_id,
        timestamp=timestamp,
        prior_hmac=prior,
        delivery_mode=_optional_str(row, "delivery_mode"),
        provider=_optional_str(row, "provider"),
        model_id=_optional_str(row, "model_id"),
        agent_id=_optional_str(row, "agent_id"),
        capability_profile_digest=_optional_str(row, "capability_profile_digest"),
    )


def _optional_str(row: dict[str, object], key: str) -> str | None:
    value = row.get(key)
    return value if isinstance(value, str) else None


def wire_evidence_for(
    workspace_root: Path,
    run_id: str,
    *,
    tool_name: str | None = None,
    secret: str | None,
    params_digest: str | None = None,
    agent_id: str | None = None,
) -> bool:
    """Return ``True`` iff a verified ``tools/call`` record backs ``run_id``.

    When ``tool_name`` is given, only a record whose ``tool_name`` matches
    (case-insensitive substring match, so a canonical tool name like
    ``ralph_submit_md_artifact`` matches a lookup for ``"artifact"``)
    counts. ``tool_name=None`` matches any ``tools/call`` record for the run
    — the general "did the agent dial the MCP server at all" signal.

    When ``params_digest`` is given, only a record whose ``params_digest``
    exactly matches is counted (the multimodal smoke grader uses this to
    enforce the replay-hop contract: a verified ``read_media`` call with
    the fixture path is not enough — there must ALSO be a verified
    ``read_media`` call whose arguments' digest equals the digest of
    ``{"path": <server-minted handle>}``, which the agent could only have
    issued after consuming the first response).

    Returns ``False`` (never grades ``WIRE``) when ``secret`` is ``None`` or
    the chain fails to verify — an unverifiable ledger backs nothing.
    """
    if secret is None:
        return False
    intact, calls = _ledger_index(workspace_root, secret).snapshot()
    if not intact:
        return False
    for row in calls:
        if row.get("run_id") != run_id:
            continue
        if params_digest is not None:
            row_digest = row.get("params_digest")
            if not isinstance(row_digest, str) or row_digest != params_digest:
                continue
        if agent_id is not None and row.get("agent_id") != agent_id:
            continue
        if tool_name is None:
            return True
        row_tool = row.get("tool_name")
        if isinstance(row_tool, str) and tool_name.lower() in row_tool.lower():
            return True
    return False
