"""Regression tests: wire-ledger cost must not grow with the ledger's length.

A long run chains a health-probe handshake onto the ledger every few
seconds. Appending re-read the whole file to find the chain head, and the
per-tick completion check re-verified the whole chain, so Ralph slowed
down the longer it ran. These tests pin that the chain head is read from
the tail, that each row is HMAC-checked once, and that the incremental
view still fails closed on a rewritten ledger.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from ralph.mcp.server import _wire_ledger
from ralph.mcp.server._wire_ledger import (
    _ledger_path,
    _read_last_hmac,
    append_wire_record,
    verify_chain,
    wire_evidence_for,
)
from ralph.mcp.server._wire_ledger_index import GENESIS_HMAC, WireLedgerIndex

if TYPE_CHECKING:
    from pathlib import Path

_SECRET = "s3cr3t"


def _append(root: Path, *, tool_name: str = "t", run_id: str = "run-1") -> None:
    append_wire_record(
        root,
        method="tools/call",
        tool_name=tool_name,
        params={"tool": tool_name},
        run_id=run_id,
        secret=_SECRET,
    )


def _counting_index(path: Path, calls: list[int]) -> WireLedgerIndex:
    def expected(row: dict[str, object], prior: str) -> str | None:
        calls.append(1)
        return _wire_ledger._expected_row_hmac(_SECRET, row, prior)

    return WireLedgerIndex(path, expected)


def test_read_last_hmac_scans_backwards_past_a_chunk_boundary(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    filler = json.dumps({"hmac": "a" * 64, "pad": "x" * 300})
    last = json.dumps({"hmac": "b" * 64})
    lines = [filler] * 400 + [last, "not json", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    assert path.stat().st_size > _wire_ledger._TAIL_CHUNK_BYTES

    assert _read_last_hmac(path) == "b" * 64


def test_read_last_hmac_of_missing_or_rowless_ledger_is_genesis(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    assert _read_last_hmac(path) == GENESIS_HMAC
    path.write_text("garbage\n\n[1, 2]\n", encoding="utf-8")
    assert _read_last_hmac(path) == GENESIS_HMAC


def test_each_row_is_hmac_checked_once_across_lookups(tmp_path: Path) -> None:
    for _ in range(5):
        _append(tmp_path)
    calls: list[int] = []
    index = _counting_index(_ledger_path(tmp_path, _SECRET), calls)

    assert index.snapshot()[0] is True
    assert index.snapshot()[0] is True
    assert len(calls) == 5

    _append(tmp_path, tool_name="declare_complete")
    intact, rows = index.snapshot()
    assert intact is True
    assert len(calls) == 6
    assert [row["tool_name"] for row in rows][-1] == "declare_complete"


def test_partial_trailing_line_is_seen_but_not_committed(tmp_path: Path) -> None:
    _append(tmp_path)
    _append(tmp_path, tool_name="declare_complete")
    path = _ledger_path(tmp_path, _SECRET)
    full = path.read_bytes()
    cut = full.rindex(b"{")
    path.write_bytes(full[:-1])  # last row present but its newline not yet written
    index = _counting_index(path, [])
    intact, rows = index.snapshot()
    assert intact is True
    assert [row["tool_name"] for row in rows] == ["t", "declare_complete"]

    path.write_bytes(full[: cut + 10])  # an append caught mid-write
    intact, rows = index.snapshot()
    assert intact is True
    assert [row["tool_name"] for row in rows] == ["t"]

    path.write_bytes(full)
    intact, rows = index.snapshot()
    assert intact is True
    assert [row["tool_name"] for row in rows] == ["t", "declare_complete"]


def test_same_size_forgery_of_a_verified_row_still_breaks_the_chain(tmp_path: Path) -> None:
    _append(tmp_path)
    _append(tmp_path, tool_name="declare_complete")
    assert wire_evidence_for(tmp_path, "run-1", tool_name="declare_complete", secret=_SECRET)

    path = _ledger_path(tmp_path, _SECRET)
    lines = path.read_text(encoding="utf-8").splitlines()
    forged = json.loads(lines[1])
    forged["hmac"] = "0" * 64
    lines[1] = json.dumps(forged, sort_keys=True)
    stat = path.stat()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_size == stat.st_size

    assert verify_chain(tmp_path, _SECRET) is False
    assert not wire_evidence_for(tmp_path, "run-1", tool_name="declare_complete", secret=_SECRET)


def test_truncated_or_replaced_ledger_is_reverified_from_scratch(tmp_path: Path) -> None:
    _append(tmp_path, tool_name="declare_complete")
    assert wire_evidence_for(tmp_path, "run-1", tool_name="declare_complete", secret=_SECRET)

    path = _ledger_path(tmp_path, _SECRET)
    path.unlink()
    assert verify_chain(tmp_path, _SECRET) is True
    assert not wire_evidence_for(tmp_path, "run-1", tool_name="declare_complete", secret=_SECRET)

    _append(tmp_path, tool_name="other")
    assert verify_chain(tmp_path, _SECRET) is True
    assert wire_evidence_for(tmp_path, "run-1", tool_name="other", secret=_SECRET)
    assert not wire_evidence_for(tmp_path, "run-1", tool_name="declare_complete", secret=_SECRET)


def test_append_after_a_verified_lookup_keeps_chaining(tmp_path: Path) -> None:
    _append(tmp_path)
    assert verify_chain(tmp_path, _SECRET) is True
    _append(tmp_path, run_id="run-2")
    _append(tmp_path, run_id="run-2", tool_name="declare_complete")

    assert verify_chain(tmp_path, _SECRET) is True
    assert wire_evidence_for(tmp_path, "run-2", tool_name="declare_complete", secret=_SECRET)
    assert not wire_evidence_for(tmp_path, "run-1", tool_name="declare_complete", secret=_SECRET)
