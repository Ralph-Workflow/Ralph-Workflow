from __future__ import annotations

import contextlib
import json
import pathlib
import socket
import threading
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Protocol, TypeGuard
from unittest import mock

import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._handover_protocol import (
    compute_tool_set_hash,
    create_handover_socketpair,
)
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess

pytestmark = pytest.mark.timeout_seconds(15)

_ENDPOINT = "http://127.0.0.1:9137/mcp"
_OLD_PID = 1001
_CANDIDATE_PID = 2002
_OLD_STAGE_ID = "stage-old"
_CANDIDATE_STAGE_ID = "stage-candidate"

_PROMPT_READ_BUDGET_S = 1.0
_WORKER_BUDGET_S = 10.0

_TOOLS_BASE: list[dict[str, object]] = [
    {"name": "read_file", "description": "Read a file", "inputSchema": {"type": "object"}},
    {"name": "write_file", "description": "Write a file", "inputSchema": {"type": "object"}},
]
_TOOLS_DRIFTED: list[dict[str, object]] = [
    {"name": "read_file", "description": "Read a file", "inputSchema": {"type": "object"}},
    {"name": "other_tool", "description": "Different schema", "inputSchema": {}},
]


def _is_json_object(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict)


class _FakeProcess:
    def __init__(self, poll_result: int | None = None, pid: int = _OLD_PID) -> None:
        self._poll_result = poll_result
        self._pid = pid
        self.terminate_calls = 0
        self.kill_calls = 0
        self.orphan_cleanups = 0

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        return self._poll_result

    def terminate(self, grace_period_s: float = 5.0) -> None:
        del grace_period_s
        self.terminate_calls += 1
        self._poll_result = 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self._poll_result = 0
        return 0

    def kill(self) -> None:
        self.kill_calls += 1
        self._poll_result = -9

    def cleanup_orphans(self) -> None:
        self.orphan_cleanups += 1


def _make_inner(
    tmp_path: pathlib.Path,
    *,
    label: str,
    pid: int,
    stage_id: str,
    poll_result: int | None = None,
) -> StandaloneMcpProcess:
    session_file = tmp_path / f"{label}-session.json"
    session_file.write_text("{}", encoding="utf-8")
    return StandaloneMcpProcess(
        endpoint=_ENDPOINT,
        process=_FakeProcess(poll_result=poll_result, pid=pid),
        session_file=session_file,
        stage_id=stage_id,
    )


def _serve_private_channel(
    child_sock: socket.socket,
    *,
    reported_pid: int,
    reported_spawn_id: str,
    tools: list[dict[str, object]] | None,
) -> None:
    with child_sock:
        if tools is None:
            return
        buffer = b""
        while True:
            try:
                chunk = child_sock.recv(4096)
            except (TimeoutError, OSError):
                return
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                raw_request: object = json.loads(line.decode("utf-8"))
                if not _is_json_object(raw_request):
                    continue
                method = raw_request.get("method")
                if method == "initialize":
                    result: dict[str, object] = {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "ralph-mcp", "version": "0.1.0"},
                        "pid": reported_pid,
                        "spawn_id": reported_spawn_id,
                        "session_id": "",
                        "broker_secret_fingerprint": None,
                    }
                elif method == "tools/list":
                    result = {"tools": tools}
                else:
                    result = {}
                response = {"jsonrpc": "2.0", "id": raw_request.get("id"), "result": result}
                with contextlib.suppress(OSError):
                    child_sock.sendall(json.dumps(response).encode("utf-8") + b"\n")


def _private_candidate(
    tmp_path: pathlib.Path,
    *,
    reported_pid: int = _CANDIDATE_PID,
    reported_spawn_id: str = _CANDIDATE_STAGE_ID,
    tools: list[dict[str, object]] | None = None,
) -> StandaloneMcpProcess:
    parent_sock, child_sock = create_handover_socketpair()
    candidate = _make_inner(
        tmp_path,
        label="candidate",
        pid=_CANDIDATE_PID,
        stage_id=_CANDIDATE_STAGE_ID,
    )
    candidate.handover_parent_sock = parent_sock
    responder_kwargs: dict[str, object] = {
        "reported_pid": reported_pid,
        "reported_spawn_id": reported_spawn_id,
        "tools": _TOOLS_BASE if tools is None else tools,
    }
    worker = threading.Thread(
        target=_serve_private_channel,
        args=(child_sock,),
        kwargs=responder_kwargs,
        daemon=True,
    )
    worker.start()
    return candidate


def _silent_candidate(tmp_path: pathlib.Path) -> StandaloneMcpProcess:
    parent_sock, child_sock = create_handover_socketpair()
    candidate = _make_inner(
        tmp_path,
        label="candidate",
        pid=_CANDIDATE_PID,
        stage_id=_CANDIDATE_STAGE_ID,
    )
    candidate.handover_parent_sock = parent_sock
    child_sock.close()
    return candidate


class _RestartFn(Protocol):
    def __call__(self, *, make_before_break: bool = False) -> StandaloneMcpProcess: ...


def _counting_restart_fn(
    candidates: list[StandaloneMcpProcess],
) -> tuple[_RestartFn, list[int]]:
    remaining = list(candidates)
    spawns: list[int] = []

    def _restart(
        *,
        make_before_break: bool = False,
    ) -> StandaloneMcpProcess:
        del make_before_break
        spawns.append(len(spawns) + 1)
        if not remaining:
            raise McpServerError("no prepared candidate left", restart_count=0)
        return remaining.pop(0)

    return _restart, spawns


def _always_failing_probe(_endpoint: str, _timeout: timedelta) -> None:
    raise ConnectionResetError("probe failed")


def _build_bridge(
    tmp_path: pathlib.Path, candidates: list[StandaloneMcpProcess]
) -> lifecycle.RestartAwareMcpBridge:
    old_inner = _make_inner(tmp_path, label="old", pid=_OLD_PID, stage_id=_OLD_STAGE_ID)
    restart_fn, _spawns = _counting_restart_fn(candidates)
    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-replacement-paths",
        probe_fn=_always_failing_probe,
        contention_signal_producer=lambda: False,
        sleep_fn=lambda _seconds: None,
        tool_entries_fn=lambda _endpoint, _timeout: list(_TOOLS_BASE),
    )
    bridge.capture_tool_hash_baseline()
    return bridge


@contextlib.contextmanager
def _shared_endpoint_is_not_identity_evidence() -> Iterator[None]:

    def _forbidden(*_args: object, **_kwargs: object) -> list[str]:
        raise AssertionError(
            "the shared endpoint was consulted for candidate identity; that answer comes "
            "from the OLD child while it is still serving"
        )

    with mock.patch.object(lifecycle, "_http_tools_list_names", _forbidden):
        yield


@contextlib.contextmanager
def _blocked_private_verification(
    bridge: lifecycle.RestartAwareMcpBridge,
) -> Iterator[threading.Event]:
    entered = threading.Event()
    release = threading.Event()

    def _block(_candidate: StandaloneMcpProcess) -> bool:
        entered.set()
        release.wait(timeout=_WORKER_BUDGET_S)
        return False

    with mock.patch.object(bridge, "_verify_candidate", _block):
        try:
            yield entered
        finally:
            release.set()


def _assert_agent_path_prompt(bridge: lifecycle.RestartAwareMcpBridge) -> None:
    observed: dict[str, object] = {}
    done = threading.Event()

    def _read() -> None:
        try:
            observed["pid"] = bridge.process.pid
            observed["endpoint"] = bridge.endpoint_uri()
        finally:
            done.set()

    reader = threading.Thread(target=_read, name="agent-path-reader", daemon=True)
    reader.start()
    assert done.wait(timeout=_PROMPT_READ_BUDGET_S), (
        "E4.1 violated: bridge.process / endpoint_uri stayed blocked for "
        f"{_PROMPT_READ_BUDGET_S}s while a candidate was starting up"
    )
    reader.join(timeout=_PROMPT_READ_BUDGET_S)
    assert not reader.is_alive()
    assert observed == {"pid": _OLD_PID, "endpoint": _ENDPOINT}


def _drive(target: Callable[[], object]) -> tuple[threading.Thread, threading.Event]:
    done = threading.Event()

    def _run() -> None:
        try:
            target()
        finally:
            done.set()

    thread = threading.Thread(target=_run, name="replacement-initiator", daemon=True)
    thread.start()
    return thread, done


def _arm_health_restart(bridge: lifecycle.RestartAwareMcpBridge) -> None:
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.restart_count == 0


def test_health_path_starts_candidate_without_holding_the_bridge_lock(
    tmp_path: pathlib.Path,
) -> None:
    bridge = _build_bridge(
        tmp_path,
        [_make_inner(tmp_path, label="c", pid=_CANDIDATE_PID, stage_id=_CANDIDATE_STAGE_ID)],
    )
    _arm_health_restart(bridge)

    outcome: list[bool] = []
    with _blocked_private_verification(bridge) as entered:
        thread, done = _drive(lambda: outcome.append(bridge.check_health_and_restart_if_needed()))
        assert entered.wait(timeout=_WORKER_BUDGET_S), "private verification never ran"
        _assert_agent_path_prompt(bridge)

    assert done.wait(timeout=_WORKER_BUDGET_S), "health replacement never finished"
    thread.join(timeout=_WORKER_BUDGET_S)
    assert not thread.is_alive()
    assert outcome == [False]
    assert bridge.process.pid == _OLD_PID
    assert bridge.restart_count == 1


def test_reset_tool_registry_starts_candidate_without_holding_the_bridge_lock(
    tmp_path: pathlib.Path,
) -> None:
    bridge = _build_bridge(
        tmp_path,
        [_make_inner(tmp_path, label="c", pid=_CANDIDATE_PID, stage_id=_CANDIDATE_STAGE_ID)],
    )

    with _shared_endpoint_is_not_identity_evidence():
        with _blocked_private_verification(bridge) as entered:
            thread, done = _drive(bridge.reset_tool_registry)
            assert entered.wait(timeout=_WORKER_BUDGET_S), "private verification never ran"
            _assert_agent_path_prompt(bridge)

        assert done.wait(timeout=_WORKER_BUDGET_S), "tool-registry reset never finished"
        thread.join(timeout=_WORKER_BUDGET_S)
        assert not thread.is_alive()

    assert bridge.process.pid == _OLD_PID
    assert bridge.tool_registry_resets == 1


def test_reset_refuses_candidate_with_wrong_spawn_identity(tmp_path: pathlib.Path) -> None:
    candidate = _private_candidate(tmp_path, reported_spawn_id="stage-somebody-else")
    bridge = _build_bridge(tmp_path, [candidate])
    old_proc = bridge.process

    with _shared_endpoint_is_not_identity_evidence():
        bridge.reset_tool_registry()

    assert bridge.process.pid == _OLD_PID
    assert bridge.process is old_proc
    candidate_proc = candidate.process
    assert isinstance(candidate_proc, _FakeProcess)
    assert candidate_proc.terminate_calls >= 1, "refused candidate was not reaped"
    assert candidate_proc.orphan_cleanups >= 1


def test_reset_refuses_candidate_with_wrong_tool_set_hash(tmp_path: pathlib.Path) -> None:
    candidate = _private_candidate(tmp_path, tools=_TOOLS_DRIFTED)
    bridge = _build_bridge(tmp_path, [candidate])
    baseline_hash = compute_tool_set_hash(_TOOLS_BASE)
    assert bridge._expected_tool_hash == baseline_hash
    old_proc = bridge.process

    with _shared_endpoint_is_not_identity_evidence():
        bridge.reset_tool_registry()

    assert bridge.process.pid == _OLD_PID
    assert bridge.process is old_proc
    candidate_proc = candidate.process
    assert isinstance(candidate_proc, _FakeProcess)
    assert candidate_proc.terminate_calls >= 1, "drifted candidate was not reaped"


def test_reset_refuses_candidate_without_private_readiness(tmp_path: pathlib.Path) -> None:
    candidate = _silent_candidate(tmp_path)
    bridge = _build_bridge(tmp_path, [candidate])
    old_proc = bridge.process

    with _shared_endpoint_is_not_identity_evidence():
        bridge.reset_tool_registry()

    assert bridge.process.pid == _OLD_PID
    assert bridge.process is old_proc
    candidate_proc = candidate.process
    assert isinstance(candidate_proc, _FakeProcess)
    assert candidate_proc.terminate_calls >= 1, "unready candidate was not reaped"


def _expected_alias() -> str:
    server = lifecycle._ALIAS_VERIFY_SERVER_NAME
    tool = lifecycle._ALIAS_VERIFY_TOOL_NAME
    assert isinstance(server, str)
    assert isinstance(tool, str)
    return f"mcp__{server}__{tool}"


@contextlib.contextmanager
def _shared_endpoint_advertises_the_alias() -> Iterator[None]:

    def _advertise(*_args: object, **_kwargs: object) -> list[str]:
        return [_expected_alias()]

    with mock.patch.object(lifecycle, "_http_tools_list_names", _advertise):
        yield


def test_reset_promotes_a_privately_verified_candidate(tmp_path: pathlib.Path) -> None:
    candidate = _private_candidate(tmp_path, tools=_TOOLS_BASE)
    bridge = _build_bridge(tmp_path, [candidate])

    with _shared_endpoint_advertises_the_alias():
        bridge.reset_tool_registry()

    assert bridge.process.pid == _CANDIDATE_PID
    assert bridge.process is candidate.process
    assert bridge._bridge_generation == 2, "reset bumps the generation, promotion bumps it again"


def test_health_then_reset_produce_exactly_one_candidate(tmp_path: pathlib.Path) -> None:
    candidate = _make_inner(tmp_path, label="c", pid=_CANDIDATE_PID, stage_id=_CANDIDATE_STAGE_ID)
    restart_fn, spawns = _counting_restart_fn([candidate])
    bridge = _build_bridge(tmp_path, [])
    bridge._restart_fn = restart_fn
    _arm_health_restart(bridge)

    with _blocked_private_verification(bridge) as entered:
        thread, done = _drive(bridge.check_health_and_restart_if_needed)
        assert entered.wait(timeout=_WORKER_BUDGET_S), "private verification never ran"
        resets_before = bridge.tool_registry_resets
        generation_before = bridge._bridge_generation
        bridge.reset_tool_registry()
        _assert_agent_path_prompt(bridge)

    assert done.wait(timeout=_WORKER_BUDGET_S), "health replacement never finished"
    thread.join(timeout=_WORKER_BUDGET_S)
    assert not thread.is_alive()

    assert spawns == [1], f"expected exactly one candidate spawn, got {spawns!r}"
    assert bridge.tool_registry_resets == resets_before, "the refused reset consumed budget"
    assert bridge._bridge_generation == generation_before, (
        "a reset that changed nothing must not invalidate the in-flight candidate"
    )


def test_reset_then_health_produce_exactly_one_candidate(tmp_path: pathlib.Path) -> None:
    candidate = _make_inner(tmp_path, label="c", pid=_CANDIDATE_PID, stage_id=_CANDIDATE_STAGE_ID)
    restart_fn, spawns = _counting_restart_fn([candidate])
    bridge = _build_bridge(tmp_path, [])
    bridge._restart_fn = restart_fn
    _arm_health_restart(bridge)

    with _blocked_private_verification(bridge) as entered:
        thread, done = _drive(bridge.reset_tool_registry)
        assert entered.wait(timeout=_WORKER_BUDGET_S), "private verification never ran"
        restarts_before = bridge.restart_count
        assert bridge.check_health_and_restart_if_needed() is False
        _assert_agent_path_prompt(bridge)

    assert done.wait(timeout=_WORKER_BUDGET_S), "tool-registry reset never finished"
    thread.join(timeout=_WORKER_BUDGET_S)
    assert not thread.is_alive()

    assert spawns == [1], f"expected exactly one candidate spawn, got {spawns!r}"
    assert bridge.restart_count == restarts_before + 1
