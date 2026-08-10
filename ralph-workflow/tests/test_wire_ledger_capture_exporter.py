"""S-9 / F5: the wire-ledger-backed AGY capture table exporter.

Brief ``.agent/PRODUCT_CRITERIA.md`` F5 -- the wire ledger is the
fixture dividend. A live run deposits replayable frames with their
capture method already attached, so the provenance record in
``tests/display/_fixtures/agy_wire_provenance.md`` is written by the
recorder rather than by hand, and the B7 ``error``-frame label
flips to measured the first time a real ``error`` frame crosses the
wire.

These tests pin the deterministic export contract:
- ``collect_captures`` returns verified rows from the ledger (an
  unverifiable ledger returns ``[]`` and backs nothing).
- ``render_capture_table_markdown`` produces a stable, parseable
  markdown table that the fixture file can include verbatim.
- The B7 ``error``-frame entry stays labelled ``synthetic`` until a
  real ``error`` row is observed in a verified ledger.

The tests run with hand-built ledgers (no live MCP dispatch), so
the export contract is pinned without any external network.
"""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.server._wire_ledger import (
    WIRE_LEDGER_RELPATH,
    WireLedgerCapture,
    append_wire_record,
    collect_captures,
    render_capture_table_markdown,
    verify_chain,
)


def _append_record(
    tmp_path: Path,
    *,
    method: str,
    tool_name: str | None,
    params: dict[str, object],
    run_id: str,
    secret: str,
) -> None:
    """Append one HMAC-chained record to the workspace ledger."""
    record = append_wire_record(
        tmp_path,
        method=method,
        tool_name=tool_name,
        params=params,
        run_id=run_id,
        secret=secret,
    )
    assert record is not None


def test_collect_captures_returns_verified_rows(tmp_path: Path) -> None:
    """``collect_captures`` returns every verified capture in the ledger.

    S-9 / F5: the export source of truth. An unverifiable ledger backs
    nothing -- ``collect_captures`` returns ``[]`` so the exporter
    cannot promote a tampered row to the fixture file.
    """
    secret = "fixture-secret"
    _append_record(
        tmp_path,
        method="initialize",
        tool_name=None,
        params={"protocolVersion": "2025-06-18"},
        run_id="agy-fixture-run",
        secret=secret,
    )
    _append_record(
        tmp_path,
        method="tools/list",
        tool_name=None,
        params={},
        run_id="agy-fixture-run",
        secret=secret,
    )
    _append_record(
        tmp_path,
        method="tools/call",
        tool_name="ralph_submit_md_artifact",
        params={"artifact_type": "smoke_test_result"},
        run_id="agy-fixture-run",
        secret=secret,
    )

    captures = collect_captures(tmp_path, secret)

    methods = [c.method for c in captures]
    assert methods == ["initialize", "tools/list", "tools/call"]
    tools = [c.tool_name for c in captures]
    assert tools == [None, None, "ralph_submit_md_artifact"]


def test_collect_captures_returns_empty_for_unverifiable_ledger(tmp_path: Path) -> None:
    """An unverifiable ledger backs no captures -- ``collect_captures`` returns ``[]``.

    Pin the F2 invariant from the exporter side: an unchained ledger
    cannot promote its rows to a fixture file, the same way it cannot
    grade ``Provenance.WIRE``.
    """
    secret = "fixture-secret"
    _append_record(
        tmp_path,
        method="tools/call",
        tool_name="ralph_submit_md_artifact",
        params={"a": 1},
        run_id="run-1",
        secret=secret,
    )
    # Tamper with the row to break the chain.
    ledger_path = tmp_path / WIRE_LEDGER_RELPATH
    import json

    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    forged = json.loads(lines[0])
    forged["hmac"] = "0" * 64
    lines[0] = json.dumps(forged, sort_keys=True)
    ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert verify_chain(tmp_path, secret) is False
    assert collect_captures(tmp_path, secret) == []


def test_collect_captures_returns_empty_without_secret(tmp_path: Path) -> None:
    """No broker secret means no captures -- the unsigned-server invariant.

    F2 / A5: an unchained ledger is not a ledger. ``collect_captures``
    returns ``[]`` when ``secret`` is ``None`` so the exporter cannot
    accidentally promote unsigned rows to a "measured" fixture entry.
    """
    assert collect_captures(tmp_path, None) == []


def test_render_capture_table_markdown_is_deterministic(tmp_path: Path) -> None:
    """``render_capture_table_markdown`` produces a stable, parseable table.

    The fixture file embeds the rendered table verbatim, so any
    formatting drift is a fixture regeneration that breaks the
    deterministic contract. The output is byte-stable for an
    ordered input: rows are emitted in input order, with the
    ISO-8601 UTC timestamp rendered to second precision.
    """
    secret = "fixture-secret"
    _append_record(
        tmp_path,
        method="initialize",
        tool_name=None,
        params={},
        run_id="run-1",
        secret=secret,
    )
    _append_record(
        tmp_path,
        method="tools/call",
        tool_name="ralph_submit_md_artifact",
        params={"artifact_type": "smoke_test_result"},
        run_id="run-1",
        secret=secret,
    )

    captures = collect_captures(tmp_path, secret)
    rendered = render_capture_table_markdown(captures, run_id="run-1")

    assert rendered.startswith("| method | tool_name | run_id | captured-at (UTC) |")
    assert "| --- | --- | --- | --- |" in rendered
    assert "tools/call" in rendered
    assert "ralph_submit_md_artifact" in rendered
    # The exporter must NOT invent rows for non-existent captures.
    assert "error" not in rendered, (
        "B7 / F5: the ``error``-frame row is synthetic until a real "
        "capture exists. The exporter must not invent one."
    )


def test_render_capture_table_markdown_filters_by_run_id(tmp_path: Path) -> None:
    """A scoped ``run_id`` keeps multi-run ledgers from bleeding into each other.

    The MCP ledger is shared across runs in a single workspace. The
    exporter's ``run_id=`` filter ensures the planning phase's capture
    table does not include the smoke phase's frames (and vice versa).
    """
    secret = "fixture-secret"
    _append_record(
        tmp_path,
        method="tools/call",
        tool_name="ralph_submit_md_artifact",
        params={},
        run_id="planning",
        secret=secret,
    )
    _append_record(
        tmp_path,
        method="tools/call",
        tool_name="declare_complete",
        params={},
        run_id="smoke",
        secret=secret,
    )

    captures = collect_captures(tmp_path, secret)

    planning_table = render_capture_table_markdown(captures, run_id="planning")
    smoke_table = render_capture_table_markdown(captures, run_id="smoke")

    assert "ralph_submit_md_artifact" in planning_table
    assert "declare_complete" not in planning_table
    assert "declare_complete" in smoke_table
    assert "ralph_submit_md_artifact" not in smoke_table


def test_wire_ledger_capture_keeps_b7_error_frame_synthetic(tmp_path: Path) -> None:
    """B7: an ``error``-frame capture entry stays labelled synthetic until a real one is recorded.

    The brief's explicit instruction: do not invent a payload shape
    and present it as measured. The exporter only emits rows for
    verified wire-ledger captures; an absent ``error`` row in the
    ledger therefore means no ``error`` entry in the fixture table.
    """
    secret = "fixture-secret"
    _append_record(
        tmp_path,
        method="tools/call",
        tool_name="ralph_submit_md_artifact",
        params={"artifact_type": "smoke_test_result"},
        run_id="run-1",
        secret=secret,
    )

    captures = collect_captures(tmp_path, secret)
    rendered = render_capture_table_markdown(captures)

    methods = [c.method for c in captures]
    assert "error" not in methods, (
        "no real ``error`` frame has been captured for AGY; the "
        "fixture must keep the ``error`` row labelled synthetic."
    )
    # The rendered table does not invent an ``error`` row.
    assert "| error |" not in rendered


def test_wire_ledger_capture_from_row_rejects_malformed() -> None:
    """``WireLedgerCapture.from_row`` returns ``None`` for malformed rows.

    The chain verifier already rejects unparseable rows, but the
    exporter is the second line of defence: a malformed row that
    somehow slipped past the verifier cannot promote to a fixture
    capture either.
    """

    # Missing hmac -- build the row via the ledger writer so the
    # typing is consistent with the production code path. We then
    # strip the hmac field before handing the dict to ``from_row``
    # so the malformed-row path is exercised without type-ignore
    # suppression markers (which the type-ignore policy forbids in
    # test files).
    secret = "fixture-secret"
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        append_wire_record(
            tmp_path,
            method="tools/call",
            tool_name="ralph_submit_md_artifact",
            params={"artifact_type": "smoke_test_result"},
            run_id="run-1",
            secret=secret,
        )
        import json as _json

        ledger_path = tmp_path / WIRE_LEDGER_RELPATH
        good_row = _json.loads(ledger_path.read_text(encoding="utf-8").splitlines()[0])
        # Build two intentionally-malformed variants from the real
        # row, keeping the type system happy.
        bad_missing_hmac = {k: v for k, v in good_row.items() if k != "hmac"}
        bad_wrong_timestamp = dict(good_row)
        bad_wrong_timestamp["timestamp"] = "not-a-number"

    assert WireLedgerCapture.from_row(bad_missing_hmac) is None
    assert WireLedgerCapture.from_row(bad_wrong_timestamp) is None


def test_wire_ledger_capture_from_row_accepts_well_formed() -> None:
    """A well-formed row produces a ``WireLedgerCapture`` instance."""
    row = {
        "method": "tools/call",
        "tool_name": "ralph_submit_md_artifact",
        "params_digest": "deadbeef",
        "run_id": "run-1",
        "timestamp": 1234567890.0,
        "hmac": "f" * 64,
    }
    capture = WireLedgerCapture.from_row(row)
    assert capture is not None
    assert capture.method == "tools/call"
    assert capture.tool_name == "ralph_submit_md_artifact"
    assert capture.run_id == "run-1"
    assert capture.timestamp == 1234567890.0
    assert capture.hmac == "f" * 64
