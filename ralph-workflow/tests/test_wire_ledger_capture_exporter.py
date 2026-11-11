from __future__ import annotations

from pathlib import Path

from ralph.mcp.server._wire_ledger import (
    WireLedgerCapture,
    _ledger_path,
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
    secret = "fixture-secret"
    _append_record(
        tmp_path,
        method="tools/call",
        tool_name="ralph_submit_md_artifact",
        params={"a": 1},
        run_id="run-1",
        secret=secret,
    )
    ledger_path = _ledger_path(tmp_path, secret)
    import json

    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    forged = json.loads(lines[0])
    forged["hmac"] = "0" * 64
    lines[0] = json.dumps(forged, sort_keys=True)
    ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert verify_chain(tmp_path, secret) is False
    assert collect_captures(tmp_path, secret) == []


def test_collect_captures_returns_empty_without_secret(tmp_path: Path) -> None:
    assert collect_captures(tmp_path, None) == []


def test_render_capture_table_markdown_is_deterministic(tmp_path: Path) -> None:
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
    assert "error" not in rendered, (
        "B7 / F5: the ``error``-frame row is synthetic until a real "
        "capture exists. The exporter must not invent one."
    )


def test_render_capture_table_markdown_filters_by_run_id(tmp_path: Path) -> None:
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
    assert "| error |" not in rendered


def test_wire_ledger_capture_from_row_rejects_malformed() -> None:

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

        ledger_path = _ledger_path(tmp_path, secret)
        good_row = _json.loads(ledger_path.read_text(encoding="utf-8").splitlines()[0])
        bad_missing_hmac = {k: v for k, v in good_row.items() if k != "hmac"}
        bad_wrong_timestamp = dict(good_row)
        bad_wrong_timestamp["timestamp"] = "not-a-number"

    assert WireLedgerCapture.from_row(bad_missing_hmac) is None
    assert WireLedgerCapture.from_row(bad_wrong_timestamp) is None


def test_wire_ledger_capture_from_row_accepts_well_formed() -> None:
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
