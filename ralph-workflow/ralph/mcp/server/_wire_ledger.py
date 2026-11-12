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
    pass


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

WIRE_LEDGER_RELPATH = Path(".agent/tmp/mcp-wire-ledger.jsonl")

_GENESIS_HMAC = GENESIS_HMAC

TAIL_CHUNK_BYTES = 64 * 1024

DEFAULT_LOCK_TIMEOUT_S: Final[float] = 1.0

_LOCK_POLL_INTERVAL_S: Final[float] = 0.01

_DROP_LOG_EVERY: Final[int] = 100

_dropped_frames = itertools.count(1)

_MAX_CACHED_INDEXES = 32
_INDEXES_LOCK = threading.Lock()
_INDEXES: OrderedDict[tuple[str, str], WireLedgerIndex] = OrderedDict()


def _ledger_path(workspace_root: Path, secret: str | None) -> Path:
    if secret is None:
        return workspace_root / WIRE_LEDGER_RELPATH
    fingerprint = hashlib.sha256(secret.encode()).hexdigest()[:32]
    return workspace_root / ".agent" / "tmp" / f"mcp-wire-ledger-{fingerprint}.jsonl"


@dataclass(frozen=True)
class WireLedgerRecord:
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
    canonical = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _params_digest(params: dict[str, object]) -> str:
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
    lines: list[str] = []
    lines.append("| method | tool_name | run_id | captured-at (UTC) |")
    lines.append("| --- | --- | --- | --- |")
    for capture in captures:
        if run_id is not None and capture.run_id != run_id:
            continue
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
    try:
        with ledger_path.open("rb") as handle:
            end = handle.seek(0, os.SEEK_END)
            carry = b""
            while end > 0:
                start = max(0, end - TAIL_CHUNK_BYTES)
                handle.seek(start)
                lines = (handle.read(end - start) + carry).split(b"\n")
                end = start
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
    if secret is None:
        return None
    try:
        ledger_path = _ledger_path(workspace_root, secret)
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = ledger_path.with_name(ledger_path.name + ".lock")
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
