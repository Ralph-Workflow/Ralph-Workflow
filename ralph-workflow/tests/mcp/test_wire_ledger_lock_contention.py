"""A busy wire-ledger lock must never fail a tool call or flood the server log.

The ledger is diagnostic evidence. Under host load a sibling writer can hold
the append lock past the acquisition deadline; that used to surface as a
JSON-RPC ``-32603`` on ``tools/call`` (agents saw ``wire_ledger_io_error:
lock contention``) and as a full traceback per frame in ``mcp-server.log``.
Contention now drops that one frame's witness, leaves the HMAC chain intact,
and keeps the log to a single bounded line.
"""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.server._wire_ledger import append_wire_record, verify_chain
from ralph.mcp.tools.bridge._registry import build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import loguru
    import pytest

_SECRET = "contention-secret"
_FAST_DEADLINE_S = 0.05
_FRAMES_KEPT = 2


def _append(root: Path) -> object:
    return append_wire_record(
        root,
        method="tools/call",
        tool_name="t",
        params={},
        run_id="run-1",
        secret=_SECRET,
        lock_timeout_s=_FAST_DEADLINE_S,
    )


def _ledger_row_count(root: Path) -> int:
    ledger = next((root / ".agent" / "tmp").glob("mcp-wire-ledger-*.jsonl"))
    return len(ledger.read_text(encoding="utf-8").splitlines())


@contextmanager
def _held_lock(root: Path) -> Iterator[None]:
    lock_file = next((root / ".agent" / "tmp").glob("mcp-wire-ledger-*.jsonl.lock"))
    with lock_file.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def test_contended_lock_drops_the_frame_without_raising_or_breaking_the_chain(
    tmp_path: Path,
) -> None:
    assert _append(tmp_path) is not None
    with _held_lock(tmp_path):
        assert _append(tmp_path) is None
    assert _append(tmp_path) is not None

    assert _ledger_row_count(tmp_path) == _FRAMES_KEPT
    assert verify_chain(tmp_path, _SECRET) is True


def _session() -> AgentSession:
    return AgentSession(
        session_id="session-1",
        run_id="run-1",
        drain="development",
        capabilities={"WorkspaceRead", "RunReportProgress"},
        broker_secret=_SECRET,
    )


def _server(root: Path) -> McpServer:
    session = _session()
    workspace = FsWorkspace(root)
    return McpServer(session, workspace, build_ralph_tool_registry(session, workspace))


def test_tools_call_succeeds_while_the_ledger_lock_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ralph.mcp.server._wire_ledger.DEFAULT_LOCK_TIMEOUT_S", _FAST_DEADLINE_S)
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    server = _server(tmp_path)
    state = ServerState.RUNNING
    # Any first frame creates the lock file the second call will find held.
    server.handle_request(JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=1), state)

    with _held_lock(tmp_path):
        response, _ = server.handle_request(
            JsonRpcRequest(
                jsonrpc="2.0",
                method="tools/call",
                msg_id=2,
                params={"name": "read_file", "arguments": {"path": "a.txt"}},
            ),
            state,
        )

    assert response is not None
    assert response.error is None


def test_a_failed_ledger_append_logs_without_a_traceback(tmp_path: Path) -> None:
    (tmp_path / ".agent").mkdir()
    (tmp_path / ".agent" / "tmp").write_text("not a directory", encoding="utf-8")
    server = _server(tmp_path)
    carried_traceback: list[bool] = []

    def _sink(message: loguru.Message) -> None:
        rec = message.record
        carried_traceback.append(rec["exception"] is not None)

    sink_id = logger.add(_sink, level="DEBUG")
    try:
        server.handle_request(
            JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized", msg_id=None),
            ServerState.RUNNING,
        )
    finally:
        logger.remove(sink_id)

    assert carried_traceback
    assert not any(carried_traceback)
