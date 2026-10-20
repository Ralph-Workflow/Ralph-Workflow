"""Replacement initiation through the two PUBLIC entry paths.

The bridge has two public ways to start a replacement — the health call
``check_health_and_restart_if_needed()`` and the recovery call
``reset_tool_registry()`` — and both must satisfy the same contract:

E4.1 (short lock)
    The candidate spawn and the candidate preflight run OFF the bridge lock,
    so an agent reading ``bridge.process`` or the endpoint is never queued
    behind a replacement. Measured here with ``threading.Event`` waits and
    bounded thread joins, never ``time.sleep``.

E1.1 / E1.9 (private candidate channel)
    Every promotion on both paths is gated by ``_verify_candidate``, which
    proves the candidate's spawn id, pid, session id and tool-set schema hash
    over the PRIVATE socketpair. A preflight over the shared endpoint is
    worthless during the overlap because the OLD child answers there, so the
    tests additionally forbid the shared endpoint from being consulted at all.

E2.7 (one candidate)
    Concurrent initiators share ONE candidate slot. Two initiators racing
    produce exactly one spawned candidate, and the loser is refused without
    consuming budget or bumping the generation.

Every test here is fully faked: no real subprocess, no real network, no
wall-clock sleeps. The private channel is a real ``AF_UNIX`` socketpair
driven by a daemon thread, which is the same seam
``tests/mcp/test_lifecycle_tool_hash_and_classification.py`` uses.
"""

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

#: An agent-path read that has not completed within this budget was queued
#: behind the candidate startup, which is the E4.1 defect under test.
_PROMPT_READ_BUDGET_S = 1.0
#: Upper bound on how long a test waits for a thread it is driving to notice
#: an event or finish. Every wait is bounded, so a regression fails the test
#: instead of hanging the suite.
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
    """Narrow a decoded JSON frame to the object shape the responder reads."""
    return isinstance(value, dict)


class _FakeProcess:
    """Process double with no OS process behind it and no wall-clock waits."""

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
    """Build a ``StandaloneMcpProcess`` whose session file lives under ``tmp_path``."""
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
    """Answer the private ``initialize`` + ``tools/list`` exchange.

    ``tools=None`` models a candidate whose private readiness channel exists
    but never proves readiness: the socket is closed without a reply, which is
    exactly what the parent sees when a candidate dies mid-preflight.
    """
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
    """A candidate whose private readiness channel is a real socketpair.

    The returned process owns the parent end; a daemon thread plays the child
    end. Every field the parent validates — spawn id, pid, session id and the
    canonical tool-set schema hash — is caller-controlled so a test can make
    exactly one of them wrong.
    """
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
    """A candidate whose private channel never answers: no private readiness."""
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
    """The ``restart_fn`` seam, with or without the make-before-break keyword."""

    def __call__(self, *, make_before_break: bool = False) -> StandaloneMcpProcess: ...


def _counting_restart_fn(
    candidates: list[StandaloneMcpProcess],
) -> tuple[_RestartFn, list[int]]:
    """A ``restart_fn`` that hands out prepared candidates and records every spawn."""
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
    """A probe that fails non-deferrably, so the 3-miss policy escalates to a restart."""
    raise ConnectionResetError("probe failed")


def _build_bridge(
    tmp_path: pathlib.Path, candidates: list[StandaloneMcpProcess]
) -> lifecycle.RestartAwareMcpBridge:
    """Bridge with a live old child and a seeded tool-set drift baseline."""
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
    """Forbid the bridge from consulting the SHARED endpoint on the reset path.

    During the overlap the shared endpoint is answered by the OLD child, so a
    shared probe can say nothing about the candidate. This guard turns "the
    reset route consulted the shared endpoint" into a loud failure instead of a
    silent pass.
    """

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
    """Hold the private candidate verification open until the context exits.

    The context yields the Event that fires the moment the candidate reaches
    the private preflight. Releasing happens in a ``finally``, so a failing
    assertion inside the block can never leave the driver thread wedged.
    """
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
    """Assert ``bridge.process`` and the endpoint answer without blocking.

    The read happens on its own thread and is judged by a bounded ``Event``
    wait, so a bridge that holds its lock across candidate startup fails here
    instead of quietly taking longer.
    """
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
    """Run ``target`` on a daemon thread; the returned Event marks completion."""
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
    """Spend the first two probe misses so the next health call escalates."""
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.restart_count == 0


def test_health_path_starts_candidate_without_holding_the_bridge_lock(
    tmp_path: pathlib.Path,
) -> None:
    """E4.1: a candidate blocked in private verification never blocks agent reads."""
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
    # Non-terminal: the refused candidate left the old child serving.
    assert outcome == [False]
    assert bridge.process.pid == _OLD_PID
    assert bridge.restart_count == 1


def test_reset_tool_registry_starts_candidate_without_holding_the_bridge_lock(
    tmp_path: pathlib.Path,
) -> None:
    """E4.1: the reset route also runs its candidate startup off the lock."""
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
    """E1.1: a candidate that reports the wrong spawn id is never promoted."""
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
    """E1.9: a candidate whose private schema hash drifted is never promoted."""
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
    """A candidate that never answers the private channel is never promoted."""
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
    """The alias the reset route's shared-endpoint gate looks for."""
    server = lifecycle._ALIAS_VERIFY_SERVER_NAME
    tool = lifecycle._ALIAS_VERIFY_TOOL_NAME
    assert isinstance(server, str)
    assert isinstance(tool, str)
    return f"mcp__{server}__{tool}"


@contextlib.contextmanager
def _shared_endpoint_advertises_the_alias() -> Iterator[None]:
    """Let the reset route's secondary shared-endpoint alias gate succeed."""

    def _advertise(*_args: object, **_kwargs: object) -> list[str]:
        return [_expected_alias()]

    with mock.patch.object(lifecycle, "_http_tools_list_names", _advertise):
        yield


def test_reset_promotes_a_privately_verified_candidate(tmp_path: pathlib.Path) -> None:
    """The same state machine promotes once the private channel proves identity.

    Without this the three refusals above could pass for the wrong reason
    (a reset that never promotes anything). The positive case pins that the
    gate admits exactly one well-formed candidate.
    """
    candidate = _private_candidate(tmp_path, tools=_TOOLS_BASE)
    bridge = _build_bridge(tmp_path, [candidate])

    with _shared_endpoint_advertises_the_alias():
        bridge.reset_tool_registry()

    assert bridge.process.pid == _CANDIDATE_PID
    assert bridge.process is candidate.process
    assert bridge._bridge_generation == 2, "reset bumps the generation, promotion bumps it again"


def test_health_then_reset_produce_exactly_one_candidate(tmp_path: pathlib.Path) -> None:
    """E2.7: a reset arriving during an in-flight health replacement is refused."""
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
    """E2.7 mirror: a health replacement arriving during a reset is refused too."""
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
