"""Regression tests for the MCP wire ledger (Evidence Provenance F2).

``Provenance.WIRE`` is granted only by a matching, HMAC-verifiable
``tools/call`` record in ``.agent/tmp/mcp-wire-ledger.jsonl``. These tests
pin: a real dispatch through :class:`McpServer` produces a verifiable
record; an unsigned server (no broker secret) never writes one; and a
forged or unchained row is rejected by the chain verifier, never grading
``WIRE``.
"""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.server._wire_ledger import (
    WIRE_LEDGER_RELPATH,
    WireLedgerRecord,
    append_wire_record,
    verify_chain,
    wire_evidence_for,
)
from ralph.mcp.tools.coordination import ToolContent, ToolResult


class _Workspace:
    """Minimal workspace stub exposing only the ``.root`` McpServer reads."""

    def __init__(self, root: Path) -> None:
        self.root = root


class _FakeRegistry:
    """A registry whose dispatch always succeeds with a fixed text result."""

    def dispatch(
        self, tool_name: str, arguments: dict[str, object], *, host_session: object
    ) -> ToolResult:
        del tool_name, arguments, host_session
        return ToolResult(content=[ToolContent.text_content("ok")], is_error=False)

    def list_definitions(self) -> list[object]:
        return []


def _session(run_id: str, *, broker_secret: str | None) -> AgentSession:
    return AgentSession(
        session_id="sess-1",
        run_id=run_id,
        drain="development",
        broker_secret=broker_secret,
    )


def _dispatch_tools_call(
    tmp_path: Path, *, run_id: str, broker_secret: str | None, tool_name: str = "ralph_submit_md_artifact"
) -> None:
    server = McpServer(
        session=_session(run_id, broker_secret=broker_secret),
        workspace=_Workspace(tmp_path),
        registry=_FakeRegistry(),
    )
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        msg_id="1",
        params={"name": tool_name, "arguments": {"artifact_type": "smoke_test_result"}},
    )
    response, _ = server.handle_request(request, ServerState.RUNNING)
    assert response is not None
    assert response.error is None


#: S-1 (Evidence Provenance): the six request methods routed through the
#: `_dispatch_request` `handlers` dict, each with a minimal-but-valid params
#: payload so the handler itself does not short-circuit before the ledger
#: append (the append happens before `handler(request)` runs either way, but
#: a valid payload keeps these tests representative of real traffic).
_HANDLER_DICT_METHODS: tuple[tuple[str, dict[str, object]], ...] = (
    ("initialize", {}),
    ("prompts/list", {}),
    ("resources/list", {}),
    ("resources/templates/list", {}),
    ("resources/read", {"uri": "ralph://media/does-not-exist"}),
    ("tools/list", {}),
)


def test_every_handler_dict_method_appends_a_ledger_row(tmp_path: Path) -> None:
    """S-1: every one of the six non-tools/call request methods gets chained too."""
    server = McpServer(
        session=_session("run-1", broker_secret="s3cr3t"),
        workspace=_Workspace(tmp_path),
        registry=_FakeRegistry(),
    )
    for msg_id, (method, params) in enumerate(_HANDLER_DICT_METHODS, start=1):
        request = JsonRpcRequest(
            jsonrpc="2.0", method=method, msg_id=str(msg_id), params=params
        )
        response, _ = server.handle_request(request, ServerState.RUNNING)
        assert response is not None

    ledger_path = tmp_path / WIRE_LEDGER_RELPATH
    assert ledger_path.exists()
    assert verify_chain(tmp_path, "s3cr3t") is True

    import json

    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    recorded_methods = [json.loads(line)["method"] for line in lines]
    for method, _params in _HANDLER_DICT_METHODS:
        assert method in recorded_methods, f"no ledger row for {method}"


#: S-2 (Evidence Provenance G2): the two notification methods are handled
#: before the `handlers` dict in `_dispatch_request` and previously never
#: reached the ledger at all.
_NOTIFICATION_METHODS: tuple[str, ...] = (
    "notifications/initialized",
    "notifications/reset_wrapup",
)


def test_notification_methods_append_a_ledger_row(tmp_path: Path) -> None:
    """S-2: notifications/initialized and notifications/reset_wrapup are chained too."""
    server = McpServer(
        session=_session("run-1", broker_secret="s3cr3t"),
        workspace=_Workspace(tmp_path),
        registry=_FakeRegistry(),
    )
    for msg_id, method in enumerate(_NOTIFICATION_METHODS, start=1):
        request = JsonRpcRequest(jsonrpc="2.0", method=method, msg_id=str(msg_id), params={})
        response, _ = server.handle_request(request, ServerState.RUNNING)
        assert response is None  # notifications never carry a response

    ledger_path = tmp_path / WIRE_LEDGER_RELPATH
    assert ledger_path.exists()
    assert verify_chain(tmp_path, "s3cr3t") is True

    import json

    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    recorded_methods = [json.loads(line)["method"] for line in lines]
    for method in _NOTIFICATION_METHODS:
        assert method in recorded_methods, f"no ledger row for {method}"


def test_ledger_with_only_handler_dict_rows_grants_no_wire_evidence(tmp_path: Path) -> None:
    """A ledger with only initialize/tools/list/... rows (no tools/call) still
    grades below WIRE — F2's `tools/call`-only grading predicate is unchanged."""
    server = McpServer(
        session=_session("run-1", broker_secret="s3cr3t"),
        workspace=_Workspace(tmp_path),
        registry=_FakeRegistry(),
    )
    for msg_id, (method, params) in enumerate(_HANDLER_DICT_METHODS, start=1):
        request = JsonRpcRequest(
            jsonrpc="2.0", method=method, msg_id=str(msg_id), params=params
        )
        server.handle_request(request, ServerState.RUNNING)

    assert wire_evidence_for(tmp_path, "run-1", secret="s3cr3t") is False


def test_dispatch_appends_verified_wire_record(tmp_path: Path) -> None:
    _dispatch_tools_call(tmp_path, run_id="run-1", broker_secret="s3cr3t")

    ledger_path = tmp_path / WIRE_LEDGER_RELPATH
    assert ledger_path.exists()
    assert verify_chain(tmp_path, "s3cr3t") is True
    assert wire_evidence_for(tmp_path, "run-1", secret="s3cr3t") is True


def test_wire_evidence_matches_tool_name_substring(tmp_path: Path) -> None:
    _dispatch_tools_call(
        tmp_path, run_id="run-1", broker_secret="s3cr3t", tool_name="ralph_submit_md_artifact"
    )

    assert wire_evidence_for(tmp_path, "run-1", tool_name="artifact", secret="s3cr3t") is True
    assert wire_evidence_for(tmp_path, "run-1", tool_name="declare_complete", secret="s3cr3t") is False


def test_wire_evidence_scoped_to_run_id(tmp_path: Path) -> None:
    _dispatch_tools_call(tmp_path, run_id="run-a", broker_secret="s3cr3t")

    assert wire_evidence_for(tmp_path, "run-a", secret="s3cr3t") is True
    assert wire_evidence_for(tmp_path, "run-b", secret="s3cr3t") is False


def test_unsigned_server_writes_no_ledger_record(tmp_path: Path) -> None:
    """A5: RALPH_BROKER_SECRET unset -> no ledger record, never grades WIRE."""
    _dispatch_tools_call(tmp_path, run_id="run-1", broker_secret=None)

    ledger_path = tmp_path / WIRE_LEDGER_RELPATH
    assert not ledger_path.exists()
    assert wire_evidence_for(tmp_path, "run-1", secret=None) is False
    assert wire_evidence_for(tmp_path, "run-1", secret="any-secret-guessed-later") is False


def test_forged_row_breaks_the_chain(tmp_path: Path) -> None:
    append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="ralph_submit_md_artifact",
        params={"a": 1},
        run_id="run-1",
        secret="s3cr3t",
    )
    append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="declare_complete",
        params={"b": 2},
        run_id="run-1",
        secret="s3cr3t",
    )
    ledger_path = tmp_path / WIRE_LEDGER_RELPATH
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    # Forge the second row's hmac in place (simulating a tampered ledger).
    import json

    forged = json.loads(lines[1])
    forged["hmac"] = "0" * 64
    lines[1] = json.dumps(forged, sort_keys=True)
    ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert verify_chain(tmp_path, "s3cr3t") is False
    assert wire_evidence_for(tmp_path, "run-1", secret="s3cr3t") is False


def test_unchained_appended_row_is_rejected(tmp_path: Path) -> None:
    """A row inserted without going through the chain (wrong prior_hmac) fails verification."""
    append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="ralph_submit_md_artifact",
        params={"a": 1},
        run_id="run-1",
        secret="s3cr3t",
    )
    ledger_path = tmp_path / WIRE_LEDGER_RELPATH
    import json

    rogue = {
        "method": "tools/call",
        "tool_name": "declare_complete",
        "params_digest": "deadbeef",
        "run_id": "run-1",
        "timestamp": 0.0,
        "prior_hmac": "f" * 64,  # wrong: does not chain from the real prior record
        "hmac": "0" * 64,
    }
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(rogue, sort_keys=True) + "\n")

    assert verify_chain(tmp_path, "s3cr3t") is False
    assert wire_evidence_for(tmp_path, "run-1", tool_name="declare_complete", secret="s3cr3t") is False


def test_chain_links_successive_records(tmp_path: Path) -> None:
    first = append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="a",
        params={},
        run_id="run-1",
        secret="s3cr3t",
    )
    second = append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="b",
        params={},
        run_id="run-1",
        secret="s3cr3t",
    )
    assert first is not None
    assert second is not None
    assert second.prior_hmac == first.record_hmac
    assert verify_chain(tmp_path, "s3cr3t") is True


def test_append_wire_record_returns_none_without_secret(tmp_path: Path) -> None:
    record = append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="a",
        params={},
        run_id="run-1",
        secret=None,
    )
    assert record is None
    assert not (tmp_path / WIRE_LEDGER_RELPATH).exists()


def test_concurrent_appends_chain_safely(tmp_path: Path) -> None:
    """Concurrent MCP server instances writing to the same ledger must not break the chain.

    The restart-aware bridge tears down a previous server and starts a new one
    in the same turn, and the smoke harness drives a sequence of
    restart-aware turns. Two writers reading the same prior_hmac and both
    appending would leave the on-disk chain with two children of the same
    parent; ``verify_chain`` then fails at the first non-contiguous row, so
    even a fully-correct run never grades ``WIRE``. The non-blocking
    ``fcntl.flock`` in ``append_wire_record`` serializes the read + write
    so every append sees the previous record's hmac as its prior_hmac.

    Best-effort: a writer that loses the ``LOCK_NB`` race returns ``None``
    (its frame is dropped; the surviving frames in the same run still
    grade ``WIRE``). The chain MUST verify regardless of which writer
    won the race.
    """
    import threading

    barrier = threading.Barrier(2)
    results: list[WireLedgerRecord | None] = []

    def writer(label: str) -> None:
        barrier.wait(timeout=10.0)
        record = append_wire_record(
            tmp_path,
            method="tools/call",
            tool_name=f"ralph_{label}",
            params={"label": label},
            run_id="run-1",
            secret="s3cr3t",
        )
        results.append(record)

    threads = [threading.Thread(target=writer, args=(f"t{idx}",)) for idx in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)

    successful = [result for result in results if result is not None]
    assert successful, "At least one concurrent append must succeed"
    assert verify_chain(tmp_path, "s3cr3t") is True
    assert wire_evidence_for(tmp_path, "run-1", secret="s3cr3t") is True

