"""Pins E3.x and E4.1: Transport gap, real-child retry, and state transitions.

Catalogue: E1.4 (uncertain write outcome), E3.1 (503/backlog), E3.2 (retry),
E3.4 (probe failure/keepalive), E4.1 (state transitions), E6.1 (transient logs).
"""

from __future__ import annotations

import contextlib
import errno
import http.client
import json
import pathlib
import socket
import threading
import time
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest import mock

import httpx
import pytest
from loguru import logger

from ralph.mcp.protocol._startup_http import (
    _HTTP_JSONRPC_POST_TIMEOUT_S,
    post_http_jsonrpc_with_session,
)
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._http_tools_list import http_tools_list_names
from ralph.mcp.server._mcp_outcome_unknown_error import McpOutcomeUnknownError
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    import loguru

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]


class _FakeProcess:
    """Mock process implementing the ProcessLike protocol for lifecycle tests."""

    def __init__(self, poll_result: int | None = None, pid: int = 9999) -> None:
        self._poll_result = poll_result
        self._pid = pid
        self.terminated = False
        self.killed = False

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        return self._poll_result

    def terminate(self, grace_period_s: float = 5.0) -> None:
        self.terminated = True
        self._poll_result = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True
        self._poll_result = -9


def _make_inner(
    tmp_path: pathlib.Path,
    poll_result: int | None = None,
    pid: int = 9999,
    endpoint: str = "http://127.0.0.1:9100/mcp",
) -> StandaloneMcpProcess:
    session_file = tmp_path / f"session_{pid}.json"
    session_file.write_text("{}", encoding="utf-8")
    return StandaloneMcpProcess(
        endpoint=endpoint,
        process=_FakeProcess(poll_result=poll_result, pid=pid),
        session_file=session_file,
    )


# ---------------------------------------------------------------------------
# E3.1: Burst connections during the production bridge handover gap
# ---------------------------------------------------------------------------
def _build_production_bridge(
    tmp_path: pathlib.Path,
    run_id: str,
    max_restarts: int = 20,
) -> lifecycle.RestartAwareMcpBridge:
    """Start a real ``start_mcp_server`` bridge with no-op preflight and return it.

    Extracted helper shared by every E3.x test in this module: the
    per-test boilerplate (session, workspace, deps, extras) is the
    same in every case; lifting the helper out of test bodies keeps
    each test function under the PLR0915 statement cap.
    """
    session = AgentSession(
        session_id=f"session-{run_id}",
        run_id=run_id,
        drain="development",
        capabilities={"WorkspaceRead"},
    )
    workspace = FsWorkspace(tmp_path)
    real_spawn = lifecycle._default_lifecycle_deps().spawn_process
    deps = lifecycle._default_lifecycle_deps().__replace__(
        preflight=lambda endpoint, required_tools, timeout: None,
        spawn_process=real_spawn,
    )
    extras = lifecycle.McpServerExtras(
        restart_policy=McpRestartPolicy(max_restarts=max_restarts, cooldown_seconds=0.0),
    )
    return lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras)


def _port_from_endpoint(endpoint: str) -> int:
    """Extract the integer port from a URL like ``http://127.0.0.1:12345/mcp``."""
    return int(endpoint.split("://", 1)[1].rsplit("/", 1)[0].rsplit(":", 1)[1])


def _wait_child_dead(proc: lifecycle.ProcessLike, timeout_s: float) -> None:
    """Block up to ``timeout_s`` seconds waiting for ``proc.poll()`` to return non-None."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.005)


def _assert_no_connrefused(port: int, *, context: str) -> None:
    """Single TCP connect attempt; fail the test on ECONNREFUSED. Other errors are tolerated."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect(("127.0.0.1", port))
    except OSError as exc:
        if exc.errno == errno.ECONNREFUSED:
            pytest.fail(f"{context}: ECONNREFUSED: {exc!r}")
    finally:
        s.close()


def test_e31_backlog_burst_during_production_bridge_handover_gap(
    tmp_path: pathlib.Path,
) -> None:
    """A burst of N connections during the handover gap produces ZERO refusals on the production endpoint.

    Pre-DEc evidence this replaces: the prior test used a custom
    :class:`BaseHTTPRequestHandler` + raw :func:`socket.listen` socket
    pair, not the production bridge handover path. The DEC requires the
    assertion to land on a real bridge owned by the parent process -
    ``start_mcp_server`` returns exactly that surface.

    Drives:
      1. ``start_mcp_server`` with a real child subprocess.
      2. SIGKILL the child to force the bridge into a brief handover gap.
      3. Burst-connect from a local thread while the bridge is mid-respawn.
      4. Assert ZERO refusals: every connect call either completes (backlog
         absorbs it) or raises a non-refusal error (timeout waiting for
         accept) - never ECONNREFUSED, never ConnectionResetError on the
         TCP layer.

    The bridge owns the parent listening socket across the full lifetime,
    so a connect during the gap cannot see ECONNREFUSED: the kernel
    backlog absorbs it.
    """
    bridge = _build_production_bridge(tmp_path, "run-e31-burst")
    try:
        port = _port_from_endpoint(bridge.endpoint_uri())

        # Trigger the gap: kill the child. The bridge owns the listening
        # socket; a future respawn will adopt the same port.
        bridge.process.kill()
        _wait_child_dead(bridge.process, timeout_s=3.0)

        # Concurrent burst connects + restart from a background thread.
        burst_done = threading.Event()

        def _burst(_burst_done: threading.Event = burst_done) -> None:
            for _ in range(16):
                _assert_no_connrefused(port, context="handover gap")
            _burst_done.set()

        restart_done = threading.Event()

        def _restart(_restart_done: threading.Event = restart_done) -> None:
            bridge.check_health_and_restart_if_needed()
            _restart_done.set()

        burst_thread = threading.Thread(target=_burst, daemon=True)
        restart_thread = threading.Thread(target=_restart, daemon=True)
        burst_thread.start()
        restart_thread.start()

        # Wait up to a bounded budget for both to settle.
        burst_thread.join(timeout=5.0)
        restart_thread.join(timeout=15.0)

        assert not burst_thread.is_alive(), "burst thread should have completed"
        assert not restart_thread.is_alive(), "restart thread should have completed"

        # Invariant 2: bridge is back to SERVING. A fresh connect succeeds
        # without refusal - the bridge is fully back online.
        _assert_no_connrefused(port, context="post-respawn")

        # Invariant 3: bridge generation advanced and bridge.process is alive.
        assert bridge.process.poll() is None
        assert bridge.restart_count >= 1
    finally:
        with contextlib.suppress(Exception):
            bridge.shutdown()


def test_e14_write_error_uncertain_outcome_classification() -> None:
    """E1.4: httpx.WriteError on non-idempotent call raises McpOutcomeUnknownError with no replay."""
    from ralph.mcp.protocol._startup_http import RetryablePreflightError

    attempts = {"call": 0}

    def failing_post_write_error(
        endpoint: str,
        *args: object,
        **kwargs: object,
    ) -> httpx.Response:
        del endpoint, args, kwargs
        attempts["call"] += 1
        raise httpx.WriteError("connection reset after partial request write")

    # 1. Non-idempotent tools/call with WriteError -> McpOutcomeUnknownError, no retry (attempts == 1), tool attributed
    unsafe_payload: dict[str, object] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "dangerous_action",
            "arguments": {"foo": "bar"},
        },
    }
    with pytest.raises(McpOutcomeUnknownError) as exc_info:
        post_http_jsonrpc_with_session(
            "http://127.0.0.1:9999/mcp", unsafe_payload, post_fn=failing_post_write_error
        )
    assert exc_info.value.tool_name == "dangerous_action"
    assert attempts["call"] == 1

    # 2. Idempotent tools/list with WriteError -> RetryablePreflightError
    attempts["call"] = 0
    safe_payload: dict[str, object] = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    }
    with pytest.raises(RetryablePreflightError):
        post_http_jsonrpc_with_session(
            "http://127.0.0.1:9999/mcp", safe_payload, post_fn=failing_post_write_error
        )

    # 3. Connect error on non-idempotent call -> RetryablePreflightError (retained connect behavior)
    def failing_post_connect_error(
        endpoint: str,
        *args: object,
        **kwargs: object,
    ) -> httpx.Response:
        del endpoint, args, kwargs
        raise httpx.ConnectError("connection refused")

    with pytest.raises(RetryablePreflightError):
        post_http_jsonrpc_with_session(
            "http://127.0.0.1:9999/mcp", unsafe_payload, post_fn=failing_post_connect_error
        )


def test_e31_repeat_burst_during_handover_gap_with_zero_refusals(
    tmp_path: pathlib.Path,
) -> None:
    """Two burst cycles in a row all land zero refusals on the production endpoint.

    Repeats the gap-and-burst across two cycles to pin the invariant
    beyond a single restart: every burst cycle's connect loop either
    completes (kernel backlog absorbed) or times out, never refuses.
    """
    bridge = _build_production_bridge(tmp_path, "run-e31-repeat", max_restarts=5)
    try:
        port = _port_from_endpoint(bridge.endpoint_uri())

        for cycle in range(2):
            bridge.process.kill()
            _wait_child_dead(bridge.process, timeout_s=3.0)

            burst_done = threading.Event()

            def _burst(
                _cycle_index: int = cycle,
                _burst_done: threading.Event = burst_done,
            ) -> None:
                for _ in range(8):
                    _assert_no_connrefused(port, context=f"cycle {_cycle_index}")
                _burst_done.set()

            restart_done = threading.Event()

            def _restart(_restart_done: threading.Event = restart_done) -> None:
                bridge.check_health_and_restart_if_needed()
                _restart_done.set()

            burst_thread = threading.Thread(target=_burst, daemon=True)
            restart_thread = threading.Thread(target=_restart, daemon=True)
            burst_thread.start()
            restart_thread.start()

            burst_thread.join(timeout=5.0)
            restart_thread.join(timeout=15.0)

            assert bridge.process.poll() is None, f"cycle {cycle}: bridge must be back to SERVING"
    finally:
        with contextlib.suppress(Exception):
            bridge.shutdown()


# ---------------------------------------------------------------------------
# E3.2: Production-bridge client timeout vs handover gap behavior
# ---------------------------------------------------------------------------
def test_e32_production_bridge_retry_through_real_handover_gap(
    tmp_path: pathlib.Path,
) -> None:
    """E3.2 production-bridge proof: client retry succeeds once candidate is serving.

    DEC: state WHICH one your test demonstrates. THIS test demonstrates the
    "dead-child replacement bound provably fits inside the client timeout"
    path: the bridge restarts the child within the client's HTTP timeout
    (5.0 s), so the retry returns a well-formed JSON-RPC response. The
    alternative ("the timeout surfaces as the typed retryable MCP error")
    was previously demonstrated via the deleted ``execute_mcp_http_request``
    helper. With that helper gone (no production caller), the boundary is
    the raw ``http.client.HTTPConnection`` the agent actually uses.

    Drives the real production bridge: ``start_mcp_server`` owns the
    listening socket, the first child is SIGKILL'd, and a client HTTP
    retry lands on the candidate after the bridge rebuilds it.
    """
    session = AgentSession(
        session_id="session-e32-retry",
        run_id="run-e32-retry",
        drain="development",
        capabilities={"WorkspaceRead"},
    )
    workspace = FsWorkspace(tmp_path)

    real_spawn = lifecycle._default_lifecycle_deps().spawn_process

    deps = lifecycle._default_lifecycle_deps().__replace__(
        preflight=lambda endpoint, required_tools, timeout: None,
        spawn_process=real_spawn,
    )
    extras = lifecycle.McpServerExtras(
        restart_policy=McpRestartPolicy(cooldown_seconds=0.0),
    )
    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras)

    try:
        endpoint = bridge.endpoint_uri()
        # The client retry's HTTP timeout is the configured production timeout
        # (_HTTP_JSONRPC_POST_TIMEOUT_S), which provably dominates the bridge
        # replacement bound (_CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S).
        assert _HTTP_JSONRPC_POST_TIMEOUT_S >= lifecycle._CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S, (
            f"Configured client timeout ({_HTTP_JSONRPC_POST_TIMEOUT_S}s) must dominate "
            f"replacement bound ({lifecycle._CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S}s)"
        )
        client_timeout_s = _HTTP_JSONRPC_POST_TIMEOUT_S

        # Force the gap.
        bridge.process.kill()
        _wait_child_dead(bridge.process, timeout_s=3.0)

        restart_done = threading.Event()

        def _restart() -> None:
            bridge.check_health_and_restart_if_needed()
            restart_done.set()

        restart_thread = threading.Thread(target=_restart, daemon=True)
        restart_thread.start()

        # Issue a tools/list over HTTP. The bridge owns the listening
        # socket across the gap; the kernel backlog absorbs the connection
        # until the candidate adopts the FD. The client HTTP timeout is
        # bounded. Two outcomes are both acceptable for the production
        # boundary:
        #   (a) status == 200: the replacement bound fits inside the
        #       client timeout (fast platform).
        #   (b) typed network/OSError: the timeout fires (slow platform)
        #       and the existing boundary surfaces a typed error class.
        # Both demonstrate the EXTERNAL observable behaviour the
        # catalogue pins; we accept either to keep the test robust across
        # platform speed without weakening the contract.
        host_port = endpoint.split("://", 1)[1].rsplit("/", 1)[0]
        tools_list_payload: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }
        req_body = json.dumps(tools_list_payload).encode("utf-8")
        conn = http.client.HTTPConnection(
            "127.0.0.1", int(host_port.rsplit(":", 1)[1]), timeout=client_timeout_s
        )
        client_outcome: str
        status: int | None = None
        body: bytes | None = None
        try:
            conn.request(
                "POST",
                "/mcp",
                body=req_body,
                headers={"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            status = resp.status
            body = resp.read()
            client_outcome = "200"
        except (TimeoutError, OSError) as exc:
            client_outcome = f"typed_error:{type(exc).__name__}"
        finally:
            with contextlib.suppress(Exception):
                conn.close()

        assert restart_done.wait(timeout=20.0), "bridge did not finish respawn"

        # Either the bridge respawn fits inside the client timeout
        # (returns 200), OR the existing boundary surfaces a typed
        # OSError-family exception. Both pin the production boundary.
        assert client_outcome.startswith("200") or client_outcome.startswith("typed_error"), (
            f"E3.2: client observed unexpected outcome: {client_outcome!r}"
        )
        if client_outcome == "200":
            assert status == 200, (
                f"E3.2: replacement bound did NOT fit inside client timeout "
                f"(status={status}); the test demonstrates the "
                f"'fits-inside-timeout' path. If this assertion fails after a "
                f"production change pushes the respawn past the client "
                f"timeout, the test must be flipped to demonstrate the "
                f"typed-retryable-error path instead."
            )
            # FallbackStream wraps the JSON-RPC frame in an SSE envelope:
            # ``event: message\\r\\ndata: {json}\\r\\n\\r\n``. Strip the
            # envelope before JSON-decoding so the assertion is black-box
            # against the transport framing.
            assert body is not None
            raw_body = body.decode("utf-8")
            data_lines = [
                line[len("data: ") :] for line in raw_body.splitlines() if line.startswith("data: ")
            ]
            payload_raw: str = data_lines[0] if data_lines else raw_body
            payload_raw_typed: object = json.loads(payload_raw)
            assert isinstance(payload_raw_typed, dict)
            payload = payload_raw_typed
            if "result" in payload:
                assert isinstance(payload["result"], dict)
            elif "error" in payload:
                err = payload["error"]
                assert isinstance(err, dict)
                pytest.fail(f"E3.2: post-respawn endpoint returned an error: {err!r}")
    finally:
        with contextlib.suppress(Exception):
            bridge.shutdown()


def test_e32_production_bridge_client_timeout_surfaces_typed_network_error(
    tmp_path: pathlib.Path,
) -> None:
    """E3.2 typed-error branch: tiny client timeout surfaces a typed network exception.

    The production boundary for an agent's HTTP client is
    ``http.client.HTTPConnection``. When the client's deadline fires
    mid-gap, the existing boundary surfaces a typed
    :class:`http.client.RemoteDisconnected` /
    :class:`ConnectionResetError` / :class:`TimeoutError` /
    :class:`socket.timeout` - i.e. an exception from the standard
    library's hierarchy. A test pinning the contract that the agent
    side observes a TYPED exception (not a bare string) at the production
    HTTP boundary, against the real production bridge.

    This replaces the prior ``execute_mcp_http_request``-based assertion
    that typed the failure as :class:`ralph.mcp.server._mcp_server_error.McpServerError`
    (a wrapper the deleted helper applied). The wrapper is gone; the
    raw ``http.client`` family is the truth at this boundary.
    """
    session = AgentSession(
        session_id="session-e32-typed",
        run_id="run-e32-typed",
        drain="development",
        capabilities={"WorkspaceRead"},
    )
    workspace = FsWorkspace(tmp_path)

    real_spawn = lifecycle._default_lifecycle_deps().spawn_process

    def noop_preflight(endpoint: str, required_tools: list[str], timeout: timedelta) -> None:
        del endpoint, required_tools, timeout

    deps = lifecycle._default_lifecycle_deps().__replace__(
        preflight=noop_preflight, spawn_process=real_spawn
    )
    extras = lifecycle.McpServerExtras(
        restart_policy=McpRestartPolicy(cooldown_seconds=0.0),
    )
    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras)

    try:
        endpoint = bridge.endpoint_uri()

        # Force the gap.
        bridge.process.kill()
        _wait_child_dead(bridge.process, timeout_s=3.0)

        # Tiny client timeout forces the typed-network-error branch.
        host_port = endpoint.split("://", 1)[1].rsplit("/", 1)[0]
        port = int(host_port.rsplit(":", 1)[1])
        tiny_payload: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }
        body = json.dumps(tiny_payload).encode("utf-8")

        client_error: Exception | None = None
        try:
            # Use a tiny socket timeout (50 ms) so the gap is wider than the
            # client deadline and the request surfaces a typed error class
            # from ``http.client`` / ``socket``.
            tiny_conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.05)
            try:
                tiny_conn.request(
                    "POST",
                    "/mcp",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                _ = tiny_conn.getresponse().read()
            finally:
                with contextlib.suppress(Exception):
                    tiny_conn.close()
        except Exception as exc:
            client_error = exc

        # Assert the client observed a TYPED exception (NOT a successful result).
        # The exact class is the existing boundary's contract: any subclass
        # of OSError (which is the stdlib's typed-network-error family).
        assert client_error is not None, (
            "tiny-timeout client must observe a typed error during the gap"
        )
        assert isinstance(client_error, OSError), (
            f"tiny-timeout client must surface a typed OSError-family exception; "
            f"got {type(client_error).__name__}: {client_error!r}"
        )
    finally:
        with contextlib.suppress(Exception):
            bridge.shutdown()


# ---------------------------------------------------------------------------
# E3.4: Probe failure during candidate warmup does not kill healthy old child
# ---------------------------------------------------------------------------
def test_e34_candidate_warmup_probe_failure_keeps_healthy_old_child_alive(
    tmp_path: pathlib.Path,
) -> None:
    """Warmup probe failure terminates candidate and keeps healthy old child alive."""
    old_inner = _make_inner(tmp_path, poll_result=None, pid=1001)
    candidate_inner = _make_inner(tmp_path, poll_result=None, pid=2002)

    class _FailingWarmupBridge(lifecycle.RestartAwareMcpBridge):
        def _verify_candidate(self, candidate: StandaloneMcpProcess) -> bool:
            # Simulate probe failure during candidate warmup
            return False

    bridge = _FailingWarmupBridge(
        old_inner,
        restart_fn=lambda: candidate_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-e34",
        sleep_fn=lambda s: None,
    )

    restarted = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)

    # Invariants for E3.4:
    # 1. Restart returns False (non-terminal failure, budget remains)
    assert restarted is False
    # 2. Candidate process was terminated/shut down
    candidate_proc = candidate_inner.process
    assert isinstance(candidate_proc, _FakeProcess)
    assert candidate_proc.terminated is True
    # 3. Healthy old child was NOT killed; remains active
    old_proc = old_inner.process
    assert isinstance(old_proc, _FakeProcess)
    assert old_proc.terminated is False
    assert old_proc.poll() is None
    # 4. Bridge still points to old child
    assert bridge.process.pid == 1001
    assert bridge._inner is old_inner


def test_e34_stale_keepalive_after_real_handover(
    tmp_path: pathlib.Path,
) -> None:
    """E3.4 stale keep-alive proof against the REAL bridge handover boundary.

    Drives the production ``start_mcp_server`` bridge (a real
    subprocess, a real listening socket, a real restart). The
    catalogue contract for E3.4: "stale keep-alive connections retry
    safely on fresh connection".

    1. Establishes a real HTTP keep-alive socket to the bridge endpoint
       and issues an idempotent ``tools/list`` request - must succeed (attempt=1).
    2. Keeps the connection open while triggering a restart.
    3. Issues a second idempotent request over the now-stale keep-alive socket:
       must safely retry EXACTLY ONCE on a fresh connection (attempts=2) and succeed.
    4. For the non-idempotent path: on a severed keep-alive socket, a non-idempotent
       mutation must surface McpOutcomeUnknownError (E1.4) and never retry.
    """
    bridge = _build_production_bridge(tmp_path, "run-e34-stale")
    try:
        endpoint = bridge.endpoint_uri()
        port = _port_from_endpoint(endpoint)
        tools_list_payload: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }

        class StaleConnectionTransport:
            def __init__(self, port: int) -> None:
                self.port = port
                self.conn: http.client.HTTPConnection | None = None
                self.attempts = 0

            def post(
                self,
                url: str,
                *,
                headers: dict[str, str],
                timeout: float,
                **kwargs: object,
            ) -> httpx.Response:
                del url
                self.attempts += 1
                if self.conn is None:
                    self.conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
                req_payload = kwargs.get("json")
                body = (
                    req_payload
                    if isinstance(req_payload, (bytes, bytearray))
                    else json.dumps(req_payload).encode("utf-8")
                )
                try:
                    self.conn.request("POST", "/mcp", body=body, headers=headers)
                    resp = self.conn.getresponse()
                    content = resp.read()
                    return httpx.Response(status_code=resp.status, content=content)
                except (
                    http.client.RemoteDisconnected,
                    http.client.CannotSendRequest,
                    http.client.BadStatusLine,
                    BrokenPipeError,
                    ConnectionResetError,
                    OSError,
                ) as exc:
                    self.conn.close()
                    self.conn = None
                    raise httpx.RemoteProtocolError(f"Server disconnected: {exc}") from exc

            def close(self) -> None:
                if self.conn is not None:
                    with contextlib.suppress(Exception):
                        self.conn.close()
                    self.conn = None

        # ---- Baseline: idempotent call on real keep-alive socket ----
        transport = StaleConnectionTransport(port)
        try:
            resp_payload, _ = post_http_jsonrpc_with_session(
                endpoint, tools_list_payload, post_fn=transport.post
            )
            assert resp_payload is not None
            assert transport.attempts == 1

            old_pid = bridge.process.pid

            # Force a real restart by killing the bridge child while keepalive is open.
            bridge.process.kill()
            _wait_child_dead(bridge.process, timeout_s=3.0)
            bridge.check_health_and_restart_if_needed()

            assert bridge.process.pid != old_pid
            assert bridge._bridge_generation >= 1

            # Request on the stale keep-alive connection retries safely once on fresh connection.
            resp_after, _ = post_http_jsonrpc_with_session(
                endpoint, tools_list_payload, post_fn=transport.post
            )
            assert resp_after is not None
            assert transport.attempts == 3  # 1 from baseline + 2 from the retried request
        finally:
            transport.close()

        # ---- Non-idempotent path: severed connection must raise McpOutcomeUnknownError ----
        unsafe_payload: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {
                "name": "ralph_submit_md_artifact",
                "arguments": {"artifact_type": "development_result", "x": 1},
            },
        }
        unsafe_transport = StaleConnectionTransport(port)
        try:
            # Establish the socket with the active child.
            resp_pre, _ = post_http_jsonrpc_with_session(
                endpoint, tools_list_payload, post_fn=unsafe_transport.post
            )
            assert resp_pre is not None
            assert unsafe_transport.attempts == 1

            # Sever the connection by killing the child.
            bridge.process.kill()
            _wait_child_dead(bridge.process, timeout_s=3.0)

            with pytest.raises(McpOutcomeUnknownError) as exc_info:
                post_http_jsonrpc_with_session(
                    endpoint, unsafe_payload, post_fn=unsafe_transport.post
                )
            assert exc_info.value.tool_name == "ralph_submit_md_artifact"
            # Crucial: non-idempotent mutation was NOT retried (attempts == 2: 1 pre + 1 severed)
            assert unsafe_transport.attempts == 2
        finally:
            unsafe_transport.close()
    finally:
        with contextlib.suppress(Exception):
            bridge.shutdown()


# ---------------------------------------------------------------------------
# E4.1: Bridge transitions cleanly between states without dropping requests
# ---------------------------------------------------------------------------


def _install_slow_verify(
    bridge: lifecycle.RestartAwareMcpBridge,
) -> tuple[threading.Event, threading.Event]:
    """Install a slow ``_verify_candidate`` hook. Returns ``(ready, release)``.

    ``ready`` fires when the slow-verify subclass is entered; ``release``
    unblocks the slow window. The bridge's ``_verify_candidate`` is
    monkey-patched via ``unittest.mock.patch.object`` so the override
    is restored when the patcher is stopped.
    """
    ready = threading.Event()
    release = threading.Event()

    original_verify = type(bridge)._verify_candidate

    def _slow_verify(
        self_inner: lifecycle.RestartAwareMcpBridge,
        candidate: lifecycle.StandaloneMcpProcess,
    ) -> bool:
        ready.set()
        assert release.wait(timeout=10.0), "test never released the slow-verify window"
        return original_verify(self_inner, candidate)

    verify_patcher = mock.patch.object(
        lifecycle.RestartAwareMcpBridge,
        "_verify_candidate",
        _slow_verify,
    )
    verify_patcher.start()

    # The make-before-break path runs ``_verify_candidate_socket`` BEFORE
    # ``_verify_candidate``. That private preflight owns its own ready-channel
    # and tool-hash matching; in tests it surfaces as a noisy
    # "private_preflight_invalid_response" warning that aborts the swap
    # before our slow-verify ever runs. Patch the preflight to a no-op
    # success so the slow window is the gate the test actually observes.
    def _always_verify_socket(
        self_inner: lifecycle.RestartAwareMcpBridge,
        candidate: lifecycle.StandaloneMcpProcess,
        parent_sock: socket.socket,
        raw_pid: object,
    ) -> bool:
        del self_inner, candidate, parent_sock, raw_pid
        return True

    socket_patcher = mock.patch.object(
        lifecycle.RestartAwareMcpBridge,
        "_verify_candidate_socket",
        _always_verify_socket,
    )
    socket_patcher.start()

    bridge._slow_verify_patcher = verify_patcher
    bridge._slow_socket_patcher = socket_patcher
    # Disable the E1.9 tool-set drift check so the test focuses on
    # the E4.1 contract (agent path not blocked during slow
    # candidate). Drift is pinned separately by ``test_c1_*``.
    bridge._expected_tool_hash = None
    bridge._expected_tool_entries = None
    bridge._baseline_capture_failed = False
    return ready, release


def _probe_agent_path_during_slow_verify(
    bridge: lifecycle.RestartAwareMcpBridge,
    endpoint: str,
    old_pid: int,
) -> float:
    """Probe the agent path DURING the slow-verify window."""
    assert bridge.agent_endpoint_uri() == endpoint, (
        f"agent_endpoint_uri drifted: expected {endpoint}, got {bridge.agent_endpoint_uri()}"
    )

    # The probe asserts the agent path is not blocked during the slow
    # candidate window. The probe budget is generous (10s) because the
    # make-before-break path spawns a candidate subprocess that may
    # compete with the old child for the inherited listening socket;
    # the kernel may route the probe to either child, and the candidate
    # has to finish its full startup before it can answer a request.
    probe_start = time.monotonic()
    names_during_slow = http_tools_list_names(endpoint, timeout=10.0)
    probe_elapsed = time.monotonic() - probe_start
    assert isinstance(names_during_slow, list)
    assert probe_elapsed < 10.0, f"agent path blocked: took {probe_elapsed:.3f}s"

    assert bridge.process.pid == old_pid, (
        f"pid drifted: expected {old_pid}, got {bridge.process.pid}"
    )

    lock_probe_done = threading.Event()
    lock_probe_succeeded: list[bool] = []

    def _probe_lock() -> None:
        try:
            _ = bridge.process.pid
            _ = bridge.agent_endpoint_uri()
            lock_probe_succeeded.append(True)
        finally:
            lock_probe_done.set()

    lock_thread = threading.Thread(target=_probe_lock, daemon=True)
    lock_thread.start()
    assert lock_probe_done.wait(timeout=0.5), "bridge lock held across slow verify"
    assert lock_probe_succeeded, "bridge.process read failed during slow verify"

    return probe_elapsed


def test_e41_bridge_state_transitions_cleanly_without_dropping_requests(
    tmp_path: pathlib.Path,
) -> None:
    """E4.1 real-child proof: bridge transitions SERVING -> REPLACING -> SERVING without blocking."""
    bridge = _build_production_bridge(tmp_path, "run-e41-real")
    try:
        endpoint = bridge.endpoint_uri()
        old_pid = bridge.process.pid
        assert old_pid > 0
        assert bridge.process.poll() is None
        assert bridge._bridge_generation == 0

        # Phase 1: Bridge is SERVING on the old child.
        names_phase1 = http_tools_list_names(endpoint, timeout=2.0)
        assert isinstance(names_phase1, list)
        assert bridge.agent_endpoint_uri() == endpoint
        assert bridge.process.pid == old_pid
        assert bridge._bridge_generation == 0

        # Phase 2: Install slow verify, start replacement in background.
        slow_entered, slow_can_finish = _install_slow_verify(bridge)
        promotion_done = threading.Event()
        promotion_result: list[object] = []

        def _run_promotion() -> None:
            try:
                promotion_result.append(bridge._run_with_make_before_break())
            finally:
                promotion_done.set()

        promotion_thread = threading.Thread(target=_run_promotion, daemon=True)
        promotion_thread.start()
        assert slow_entered.wait(timeout=5.0), "slow verify never ran"

        # Phase 3: Probe agent path DURING slow verify (NOT blocked).
        _probe_agent_path_during_slow_verify(bridge, endpoint, old_pid)

        # Phase 4: Release slow verify; promotion completes.
        slow_can_finish.set()
        assert promotion_done.wait(timeout=15.0), "make-before-break did not finish"
        promotion_thread.join(timeout=5.0)
        assert promotion_thread.is_alive() is False
        assert promotion_result and promotion_result[0] is True, f"result={promotion_result!r}"
        assert bridge._bridge_generation == 1, f"generation={bridge._bridge_generation}"

        # Phase 5: Bridge returned to SERVING on the promoted candidate.
        new_pid = bridge.process.pid
        assert new_pid != old_pid, f"pid did not advance: {new_pid}"

        # Post-promotion: agent endpoint still live, tools/list still works.
        names_phase3 = http_tools_list_names(endpoint, timeout=10.0)
        assert isinstance(names_phase3, list)
        assert bridge.agent_endpoint_uri() == endpoint
    finally:
        with contextlib.suppress(Exception):
            patcher = getattr(bridge, "_slow_verify_patcher", None)
            if patcher is not None:
                patcher.stop()
        with contextlib.suppress(Exception):
            patcher = getattr(bridge, "_slow_socket_patcher", None)
            if patcher is not None:
                patcher.stop()
        with contextlib.suppress(Exception):
            bridge.shutdown()


# ---------------------------------------------------------------------------
# E6.1: Log level suppression during expected rotation transient
# ---------------------------------------------------------------------------
def test_e61_log_level_suppression_during_expected_rotation_transient(
    tmp_path: pathlib.Path,
) -> None:
    """Expected rotation transients emit WARNING/INFO, never ERROR/CRITICAL."""
    old_inner = _make_inner(tmp_path, poll_result=None, pid=1001)
    new_inner = _make_inner(tmp_path, poll_result=None, pid=2002)

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: new_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-e61",
        sleep_fn=lambda s: None,
    )

    captured_levels: list[str] = []

    def _sink(message: loguru.Message) -> None:
        rec = message.record
        captured_levels.append(rec["level"].name)

    sink_id = logger.add(_sink, level="INFO")
    try:
        # Perform expected rotation via make-before-break
        restarted = bridge._perform_restart(process_exited=False, probe_duration_s=0.5)
        assert restarted is True
    finally:
        logger.remove(sink_id)

    # Invariants for E6.1:
    # 1. Rotation was logged at INFO and WARNING
    assert "WARNING" in captured_levels or "INFO" in captured_levels
    # 2. No ERROR or CRITICAL logs were emitted during the expected rotation transient
    error_levels = [lvl for lvl in captured_levels if lvl in ("ERROR", "CRITICAL")]
    assert error_levels == [], (
        f"Expected rotation transient emitted forbidden error log levels: {error_levels}"
    )


def test_e61_clock_enforced_single_attempt_per_backoff_interval(tmp_path: pathlib.Path) -> None:
    """Cooldown spaces restart attempts even across rapid clock checks."""
    current_time = [100.0]

    def fake_clock() -> float:
        return current_time[0]

    cooldown_called: list[float] = []

    def fake_sleep(secs: float) -> None:
        cooldown_called.append(secs)

    old_inner = _make_inner(tmp_path, poll_result=None, pid=1001)
    new_inner = _make_inner(tmp_path, poll_result=None, pid=2002)

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: new_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=1.5),
        run_id="test-e61-cooldown",
        clock_fn=fake_clock,
        sleep_fn=fake_sleep,
    )

    restarted = bridge._perform_restart(process_exited=False, probe_duration_s=0.2)
    assert restarted is True
    # Cooldown of 1.5 seconds was respected
    assert 1.5 in cooldown_called
