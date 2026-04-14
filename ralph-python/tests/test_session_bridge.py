"""Unit tests for the new MCP session bridge implementation.

These exercises verify endpoint generation, audit tracking, and helper utilities
prior to wiring the bridge into the larger pipeline."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, cast

from ralph.mcp import session_bridge
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path

NEXT_GENERATION = 6


def _dummy_session(
    run_id: str = "run-a", drain: str = "development"
) -> session_bridge.AgentSession:
    return session_bridge.AgentSession(
        session_id="session-1",
        run_id=run_id,
        drain=drain,
        capabilities={"workspace.read"},
    )


def test_endpoint_lease_path_is_nested(tmp_path: Path) -> None:
    expected = tmp_path / ".agent" / "endpoint_lease.json"
    assert session_bridge.endpoint_lease_path(tmp_path) == expected


def test_next_generation_for_run_behaves(tmp_path: Path) -> None:
    run_id = "run-x"
    drain = "development"
    assert session_bridge.next_generation_for_run(tmp_path, run_id, drain) == 1

    lease_path = session_bridge.endpoint_lease_path(tmp_path)
    lease_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "endpoint": "tcp://127.0.0.1:12345",
        "run_id": run_id,
        "drain": drain,
        "generation": 5,
        "ready_at": 123456,
    }
    lease_path.write_text(json.dumps(payload))
    assert session_bridge.next_generation_for_run(tmp_path, run_id, drain) == NEXT_GENERATION

    assert session_bridge.next_generation_for_run(tmp_path, run_id, "analysis") == 1
    assert session_bridge.next_generation_for_run(tmp_path, "other-run", drain) == 1


def test_session_bridge_start_and_shutdown(tmp_path: Path) -> None:
    workspace = MemoryWorkspace(root=str(tmp_path))
    session = _dummy_session()
    bridge = session_bridge.SessionBridge(session, workspace)

    assert not bridge.is_started()
    bridge.start()
    assert bridge.is_started()
    assert bridge.endpoint_uri().startswith("tcp://127.0.0.1:")
    assert bridge.agent_endpoint_uri().startswith("http://127.0.0.1:")

    lease = bridge.endpoint_lease()
    assert lease is not None
    assert lease.endpoint == bridge.endpoint_uri()
    assert lease.run_id == session.run_id
    assert lease.drain == session.drain

    bridge.shutdown()
    assert bridge.is_shutdown()


def test_audit_trail_and_drains(tmp_path: Path) -> None:
    workspace = MemoryWorkspace(root=str(tmp_path))
    session = _dummy_session()
    bridge = session_bridge.SessionBridge(session, workspace)

    record = session_bridge.AuditRecord(
        session_id=session.session_id,
        timestamp=time.time(),
        capability="workspace.read",
        outcome="approved",
        message="test",
    )
    bridge.audit_adapter.emit(record)

    drained = bridge.drain_audit_records()
    assert drained == [record]

    trail = bridge.audit_trail()
    assert record in trail.records()

    assert bridge.drain_audit_records() == []


def test_handle_request_in_process_returns_state(tmp_path: Path) -> None:
    workspace = MemoryWorkspace(root=str(tmp_path))
    session = _dummy_session()
    bridge = session_bridge.SessionBridge(session, workspace)

    request = session_bridge.JsonRpcRequest(
        jsonrpc="2.0",
        method="ping",
        params=None,
        msg_id=1,
    )
    initial_state = cast("session_bridge.ServerState", session_bridge.ServerState.UNINITIALIZED)
    response, state = bridge.handle_request_in_process(
        request, initial_state
    )
    assert isinstance(state, session_bridge.ServerState)
    assert response is None or isinstance(response, session_bridge.JsonRpcResponse)
