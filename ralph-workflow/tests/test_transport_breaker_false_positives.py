from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._in_memory_transport import drive_request
from ralph.mcp.server._json_rpc_response import JsonRpcResponse
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._transport_repetition_tracker import (
    THRESHOLD,
    TransportRepetitionTracker,
    failure_signature,
)
from ralph.mcp.server.runtime import build_ralph_tool_registry

if TYPE_CHECKING:
    import pytest

    from ralph.mcp.server._json_rpc_request import JsonRpcRequest
    from ralph.mcp.server._server_state import ServerState

CORRUPT_CACHE_ERROR = "Tool 'read_file' failed: database disk image is malformed"

CALLER_ERROR = "Tool 'read_file' failed: unsupported encoding 'utf-77'"

TRACKER_ATTR = "ralph.mcp.server._fallback_http_handler._transport_repetition_tracker"


class _FakeWorkspace:
    pass


def _fixed_error_server(message: str) -> McpServer:
    session = AgentSession(
        session_id="breaker-test",
        run_id="breaker-run",
        drain="standalone",
        capabilities={"WorkspaceRead"},
    )
    workspace = _FakeWorkspace()
    registry = build_ralph_tool_registry(session, workspace)

    class _FixedErrorServer(McpServer):
        def handle_request(
            self, request: JsonRpcRequest, state: ServerState
        ) -> tuple[JsonRpcResponse | None, ServerState]:
            error: dict[str, object] = {"code": -32603, "message": message}
            return (
                JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id),
                state,
            )

    return _FixedErrorServer(session, workspace, registry)


def _read_file_call(msg_id: int, path: str) -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "tools/call",
            "params": {"name": "read_file", "arguments": {"path": path}},
        }
    ).encode()


def _decode(body: bytes) -> dict[str, object]:
    text = body.decode()
    _, marker, payload = text.partition("data: ")
    return json.loads(payload if marker else text)


def _drive(
    monkeypatch: pytest.MonkeyPatch, message: str, paths: list[str]
) -> list[tuple[int, dict[str, object]]]:
    monkeypatch.setattr(TRACKER_ATTR, TransportRepetitionTracker())
    server = _fixed_error_server(message)
    results: list[tuple[int, dict[str, object]]] = []
    for msg_id, path in enumerate(paths):
        status, _headers, body = drive_request(server, _read_file_call(msg_id, path))
        results.append((status, _decode(body)))
    return results


def test_reading_three_different_files_does_not_trip_the_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = _drive(
        monkeypatch,
        CALLER_ERROR,
        [".agent/master_prompt.md", ".agent/PLAN.md", ".agent/PRODUCT_CRITERIA.md"],
    )

    assert [status for status, _ in results] == [200, 200, 200]
    for _status, payload in results:
        assert payload["error"]["code"] == -32603
        assert payload["error"]["message"] == CALLER_ERROR


def test_repeated_infrastructure_fault_surfaces_the_real_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = THRESHOLD + 2
    results = _drive(monkeypatch, CORRUPT_CACHE_ERROR, [".agent/PLAN.md"] * attempts)

    assert [status for status, _ in results] == [200] * attempts
    for _status, payload in results:
        assert payload["error"]["message"] == CORRUPT_CACHE_ERROR
        assert "transport_loop_detected" not in json.dumps(payload)


def test_identical_repeated_call_still_trips_the_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = _drive(monkeypatch, CALLER_ERROR, [".agent/PLAN.md"] * THRESHOLD)

    assert [status for status, _ in results[:-1]] == [200] * (THRESHOLD - 1)
    trip_status, trip_payload = results[-1]
    assert trip_status == 503
    assert trip_payload["error"]["code"] == -32001
    assert trip_payload["error"]["message"] == "transport_loop_detected"


def test_failure_signature_separates_calls_by_their_arguments() -> None:
    plan = failure_signature(
        "tools/call",
        {"name": "read_file", "arguments": {"path": ".agent/PLAN.md"}},
        CALLER_ERROR,
    )
    criteria = failure_signature(
        "tools/call",
        {"name": "read_file", "arguments": {"path": ".agent/PRODUCT_CRITERIA.md"}},
        CALLER_ERROR,
    )

    assert plan is not None
    assert criteria is not None
    assert plan != criteria


def test_failure_signature_is_stable_for_an_identical_repeated_call() -> None:
    params = {"name": "read_file", "arguments": {"path": ".agent/PLAN.md"}}

    first = failure_signature("tools/call", dict(params), CALLER_ERROR)
    second = failure_signature("tools/call", dict(params), CALLER_ERROR)

    assert first is not None
    assert first == second


def test_failure_signature_declines_to_count_infrastructure_faults() -> None:
    params = {"name": "read_file", "arguments": {"path": ".agent/PLAN.md"}}

    assert failure_signature("tools/call", params, CORRUPT_CACHE_ERROR) is None
    assert failure_signature("tools/call", params, "-32603:disk I/O error") is None
    assert failure_signature("tools/call", params, "-32603:no space left on device") is None


def test_failure_signature_counts_caller_errors() -> None:
    params = {"name": "read_file", "arguments": {"path": ".agent/PLAN.md"}}

    assert failure_signature("tools/call", params, CALLER_ERROR) is not None
    assert failure_signature("tools/list", None, "-32603:Internal server error") is not None


def test_failure_signature_still_strips_volatile_argument_tokens() -> None:
    first = failure_signature(
        "tools/call",
        {"name": "exec", "arguments": {"trace": "aabbccdd11223344", "cmd": "ls"}},
        CALLER_ERROR,
    )
    second = failure_signature(
        "tools/call",
        {"name": "exec", "arguments": {"trace": "99887766ffeeddcc", "cmd": "ls"}},
        CALLER_ERROR,
    )

    assert first is not None
    assert first == second


def test_failure_signature_separates_distinct_tools() -> None:
    read = failure_signature("tools/call", {"name": "read_file", "arguments": {}}, CALLER_ERROR)
    write = failure_signature("tools/call", {"name": "write_file", "arguments": {}}, CALLER_ERROR)

    assert read != write
