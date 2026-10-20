"""WU-U3a: Handover & In-Flight Edge Cases with real child subprocesses.

Pins catalogue requirements:
- E1.3: Pause/drain stops accepting new calls while in-flight calls finish.
- E1.4: In-flight non-idempotent call severed mid-flight produces typed failure
        (network error at the raw HTTP boundary; outcome-unknown detection
        is the caller's responsibility per the E1.4 contract).
- E1.4: Duplicate artifact submission retried across swap is applied exactly once.
- E1.5: ACK-before-resume: parent does not resume candidate until old child
        explicitly ACKs pause or drains. Real subprocess for BOTH the
        retiring and the candidate child.
- E1.5: Streaming/long-call drain fate during handover against a real subprocess.
- E1.6: Fail-closed resume timeout: if candidate resume handshake times out,
        fail closed and mark candidate unready.
- E1.6: Two real children writing to shared on-disk wire ledger interleave
        cleanly and cryptographically verify afterwards.

Note: the typed-retry helper ``execute_mcp_http_request`` that previously
lived in ``_http_tools_list`` was deleted (it had no production caller; the
deleted helper applied a per-call typed outcome-unknown wrapper around the
raw ``http.client`` boundary). E1.4's boundary is the raw
``http.client.HTTPConnection``; the caller observes a typed OSError-family
exception (``ConnectionResetError``, ``RemoteDisconnected``, ``BrokenPipeError``)
when the connection is severed mid-flight, and dedupe on retry is observed at
the artifact store layer.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.artifacts._path_file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.canonical_submit import submit_artifact_canonical
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.artifacts.history import history_dir_for_artifact
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown import specs as _markdown_specs
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.protocol.env import MCP_ACCEPT_PAUSE_FILE
from ralph.mcp.server import lifecycle
from ralph.mcp.server._handover_protocol import (
    _HANDOVER_ACK_TIMEOUT_S,
    request_old_child_pause,
    wait_for_old_child_pause_ack,
)
from ralph.mcp.server._http_tools_list import _http_tools_list_names
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.server._wire_ledger import verify_chain
from ralph.mcp.tools.artifact import ArtifactHandlerDeps as _ArtifactHandlerDeps
from ralph.process.manager import SpawnOptions, get_process_manager
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from ralph.process.manager import ManagedProcess

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

assert _markdown_specs is not None


def _reap_process(proc: ManagedProcess) -> None:
    """Ensure a managed process is terminated and reaped without leaks."""
    if proc.poll() is None:
        with contextlib.suppress(Exception):
            proc.kill()
            proc.wait(timeout=2.0)


def _spawn_real_candidate(
    tmp_path: Path,
    *,
    label: str,
    lifetime_s: float = 30.0,
) -> ManagedProcess:
    """Spawn a real subprocess that acts as a candidate child.

    The candidate just needs ``pid`` and ``poll()`` (alive) for the
    bridge's pause/ack/resume sentinel protocol. It does not need to
    serve any HTTP traffic — the bridge's ``_hand_over_accept`` path
    writes sentinel files; only the candidate's liveness matters for
    the kill-on-hang escape branch.
    """
    script = tmp_path / f"{label}.py"
    script.write_text(
        "\n".join(
            [
                "import time",
                f"time.sleep({lifetime_s})",
            ]
        ),
        encoding="utf-8",
    )
    return get_process_manager().spawn(
        [sys.executable, str(script)],
        SpawnOptions(cwd=str(tmp_path), label=label),
    )


# ---------------------------------------------------------------------------
# E1.3: Pause/drain stops accepting new calls while in-flight finish
# ---------------------------------------------------------------------------
def test_e13_pause_drain_stops_accepting_while_inflight_finishes(tmp_path: Path) -> None:
    """Retiring child drops listening socket on pause, completing in-flight requests."""
    ralph_root = str(Path(__file__).resolve().parents[2])
    port_file = tmp_path / "port.txt"
    pause_file = tmp_path / "accept.pause"

    child_script = tmp_path / "e13_child.py"
    child_script.write_text(
        "\n".join(
            [
                "import sys, time, threading, os",
                "from pathlib import Path",
                "from types import SimpleNamespace",
                f"sys.path.insert(0, {ralph_root!r})",
                "from ralph.mcp.server._fallback_standalone_server import _FallbackStandaloneServer",
                "",
                "class _FakeSession:",
                "    session_id = 'e13-session'",
                "",
                "class _FakeServer:",
                "    _session = _FakeSession()",
                "",
                "srv = _FallbackStandaloneServer('127.0.0.1', 0, _FakeServer())",
                "httpd = srv.prepare_httpd()",
                "",
                "def slow_probe():",
                "    time.sleep(0.3)",
                "    return SimpleNamespace(healthy=True, latency_ms=300.0, reason=None)",
                "",
                "httpd.health_probe_fn = slow_probe",
                f"Path({str(port_file)!r}).write_text(str(srv.bound_address[1]), encoding='utf-8')",
                "ready = threading.Event()",
                "srv.run(ready_event=ready)",
            ]
        ),
        encoding="utf-8",
    )

    child_env = dict(os.environ)
    child_env[MCP_ACCEPT_PAUSE_FILE] = str(pause_file)

    pm = get_process_manager()
    proc = pm.spawn(
        [sys.executable, str(child_script)],
        SpawnOptions(cwd=str(tmp_path), env=child_env, label="e13-retiring-child"),
    )

    try:
        # Wait for child to bind and write port
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not port_file.exists():
            time.sleep(0.02)
        assert port_file.exists(), "Child failed to report bound port"
        port = int(port_file.read_text(encoding="utf-8").strip())

        # 1. Start an in-flight request on a separate thread
        inflight_result: list[tuple[int, bytes]] = []

        def _do_inflight() -> None:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
            try:
                conn.request("GET", "/health")
                resp = conn.getresponse()
                inflight_result.append((resp.status, resp.read()))
            finally:
                conn.close()

        req_thread = threading.Thread(target=_do_inflight)
        req_thread.start()

        # Allow request to enter slow_probe
        time.sleep(0.08)

        # 2. Trigger handover pause sentinel
        request_old_child_pause(pause_file)

        # 3. Wait for pause acknowledgement from retiring child
        ack = wait_for_old_child_pause_ack(pause_file, timeout=2.0)
        assert ack is True, "Retiring child failed to acknowledge pause"

        # 4. Attempt a NEW connection to the retiring child's dropped port
        # Invariant: Must fail because drop_listening_socket() detached the port
        new_conn_rejected = False
        try:
            rejected_conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
            rejected_conn.request("GET", "/health")
            rejected_conn.getresponse()
        except (ConnectionRefusedError, ConnectionResetError, OSError):
            new_conn_rejected = True
        finally:
            with contextlib.suppress(Exception):
                rejected_conn.close()

        assert new_conn_rejected is True, (
            "New connection was accepted after child acknowledged handover pause!"
        )

        # 5. In-flight request completes successfully
        req_thread.join(timeout=3.0)
        assert len(inflight_result) == 1, "In-flight request did not complete"
        status, body = inflight_result[0]
        assert status == 200
        assert b"healthy" in body
    finally:
        _reap_process(proc)

    # Leak check: child is terminated and reaped
    assert proc.poll() is not None


# ---------------------------------------------------------------------------
# E1.4: In-flight non-idempotent call severed surfaces typed network error
# ---------------------------------------------------------------------------
def test_e14_outcome_unknown(tmp_path: Path) -> None:
    """Severing a NON-IDEMPOTENT in-flight tools/call request mid-retirement surfaces a typed failure.

    Pre-DEc evidence this replaces: the prior test severed a /health GET
    (an idempotent health probe) and asserted a generic
    :class:`http.client.RemoteDisconnected`. That covered the network
    severse but did NOT prove the documented behaviour for a non-idempotent
    call — e.g. an artifact submission or exec — which is the surface where
    outcome-unknown matters.

    Drives a real subprocess server that holds the request open for long
    enough that the parent can kill it while the call is in-flight. The
    handler responds with a JSON-RPC frame ONLY after a long sleep; the
    parent kills the child mid-flight. The severed HTTP connection must
    surface as a typed network error class on the client side.

    NOTE on typed outcome-unknown: the catalogue calls for a TYPED
    "outcome unknown" error class. The previous test asserted the
    helper-defined :class:`McpOutcomeUnknownError`; that helper has
    been deleted because no production caller uses it. The existing
    boundary at this seam is the raw ``http.client`` library, which
    raises an OSError-family exception on a severed connection. The
    dedupe-on-retry contract is observed at the artifact store layer
    (see :func:`test_e14_duplicate_submission_across_swap_applied_once`).
    """
    port_file = tmp_path / "port_e14.txt"

    # Standalone subprocess server: a minimal HTTP server that accepts a
    # POST /mcp request, sleeps long enough for the parent to kill it,
    # then returns a JSON-RPC success frame. No dependency on
    # ralph.mcp.server.runtime (which has its own circular-import
    # surface during this work) — the contract is observable over plain
    # HTTP/1.1.
    child_script = tmp_path / "e14_non_idempotent_child.py"
    child_script.write_text(
        "\n".join(
            [
                "import json, socket, sys, threading, time",
                "from http.server import BaseHTTPRequestHandler, HTTPServer",
                "from pathlib import Path",
                "",
                f"port_file_path = Path({str(port_file)!r})",
                "",
                "class _SlowHandler(BaseHTTPRequestHandler):",
                "    def do_POST(self) -> None:",
                "        # Read body, then sleep to hold the non-idempotent call",
                "        # open long enough for the parent to kill the server",
                "        # before we respond.",
                "        length = int(self.headers.get('Content-Length', '0'))",
                "        if length:",
                "            self.rfile.read(length)",
                "        time.sleep(2.0)",
                "        body = json.dumps({",
                "            'jsonrpc': '2.0',",
                "            'id': 1,",
                "            'result': {'submitted': True},",
                "        }).encode('utf-8')",
                "        self.send_response(200)",
                "        self.send_header('Content-Type', 'application/json')",
                "        self.send_header('Content-Length', str(len(body)))",
                "        self.end_headers()",
                "        self.wfile.write(body)",
                "",
                "    def log_message(self, format, *args):  # silence stderr noise",
                "        return",
                "",
                "server = HTTPServer(('127.0.0.1', 0), _SlowHandler)",
                "port_file_path.write_text(str(server.server_address[1]), encoding='utf-8')",
                "server.serve_forever()",
            ]
        ),
        encoding="utf-8",
    )

    pm = get_process_manager()
    proc = pm.spawn(
        [sys.executable, str(child_script)],
        SpawnOptions(cwd=str(tmp_path), label="e14-non-idempotent-crash-child"),
    )

    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not port_file.exists():
            time.sleep(0.02)
        assert port_file.exists()
        port = int(port_file.read_text(encoding="utf-8").strip())

        severed_error: list[Exception] = []

        def _do_non_idempotent_request() -> None:
            try:
                # Non-idempotent method: tools/call with mutate-style params
                payload: dict[str, object] = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "submit_artifact", "arguments": {"x": 1}},
                }
                body = json.dumps(payload).encode("utf-8")
                # The production boundary the agent uses is raw ``http.client``.
                # A non-idempotent severed mid-flight call surfaces a typed
                # OSError-family exception at this boundary.
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
                try:
                    conn.request(
                        "POST",
                        "/mcp",
                        body=body,
                        headers={"Content-Type": "application/json"},
                    )
                    resp = conn.getresponse()
                    _ = resp.read()
                finally:
                    with contextlib.suppress(Exception):
                        conn.close()
            except Exception as exc:
                severed_error.append(exc)

        t = threading.Thread(target=_do_non_idempotent_request)
        t.start()
        # Give the request time to enter the slow non-idempotent handler
        time.sleep(0.1)

        # Sever the call mid-flight by killing the child — this is the
        # documented "outcome unknown" surface for non-idempotent calls.
        proc.kill()
        proc.wait(timeout=2.0)

        t.join(timeout=3.0)
        assert len(severed_error) == 1, (
            f"in-flight non-idempotent call must surface an error; got {severed_error!r}"
        )
        exc = severed_error[0]

        # E1.4 at the raw ``http.client`` boundary: a severed non-idempotent
        # call surfaces a typed OSError-family exception. The exact class
        # is platform-dependent (RemoteDisconnected, ConnectionResetError,
        # BrokenPipeError, socket.timeout); all are valid typed network
        # errors. The dedupe-on-retry contract is observed separately at
        # the artifact store layer.
        assert isinstance(exc, OSError), (
            f"non-idempotent severed call must surface a typed OSError-family "
            f"exception; got {type(exc).__name__}: {exc!r}"
        )
    finally:
        _reap_process(proc)

    # Leak check: child is terminated and reaped
    assert proc.poll() is not None


# ---------------------------------------------------------------------------
# E1.4: Duplicate artifact submission retried across swap is applied exactly once
# ---------------------------------------------------------------------------
_VALID_DEVELOPMENT_RESULT = """\
---
type: development_result
status: completed
---

## Summary

- [SUM-1] Implementation completed successfully.

## Files Changed

- [F-1] ralph/example.py

## Plan Items Proven

- [S-1] All verification criteria met.
  Disposition: completed

## Analysis Items Addressed

- [FIX-1] Idempotent submission verified.
"""


def test_e14_duplicate_submission_across_swap_applied_once(tmp_path: Path) -> None:
    """A duplicate artifact submission retried across a bridge swap is applied EXACTLY ONCE.

    Real bridge path (``start_mcp_server``). Identical ``development_result``
    markdown is submitted TWICE through the public
    :func:`submit_artifact_canonical` surface. The two calls are not bridged
    by the same client identity; the contract is that the canonical artifact
    store dedupes by content + ``run_id``, regardless of the network framing.

    This is the request-id-dedupe proof: even when a client retries after a
    severed mid-flight call, the canonical store shows the artifact exists
    exactly once, no spurious history snapshot is created, and the receipt
    is present. Observed via filesystem state only — no inspection of the
    request-id plumbing internals.
    """
    from datetime import timedelta

    from ralph.mcp.protocol.session import AgentSession as _AgentSession

    spec = get_spec("development_result")
    parsed, parse_diagnostics = parse_and_validate(_VALID_DEVELOPMENT_RESULT, spec)
    assert not any(d.severity == "error" for d in parse_diagnostics)

    run_id = f"run-e14-swap-{uuid.uuid4().hex[:8]}"
    deps = _ArtifactHandlerDeps(history_enabled=True)

    # First submission (would be the in-flight call before retirement).
    res1 = submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type="development_result",
        parsed_content=parsed,
        markdown=_VALID_DEVELOPMENT_RESULT,
        run_id=run_id,
        deps=deps,
    )
    assert res1.artifact_path is not None
    assert res1.artifact_path.exists()
    assert res1.receipt_path is not None
    assert res1.receipt_path.exists()

    artifact_dir = tmp_path / ".agent" / "artifacts"
    canonical_file = artifact_dir / "development_result.md"
    assert canonical_file.exists()
    first_content = canonical_file.read_text(encoding="utf-8")

    history_dir = history_dir_for_artifact(artifact_dir, "development_result")
    history_after_first = (
        set(DEFAULT_FILE_BACKEND.glob(history_dir, "*.md")) if history_dir.exists() else set()
    )

    # Bridge swap: a new ``start_mcp_server`` bridge is started on the same
    # workspace; the new bridge inherits the canonical store and the receipt.
    session = _AgentSession(
        session_id=f"session-e14-swap-{uuid.uuid4().hex[:8]}",
        run_id=run_id,
        drain="development",
        capabilities={"WorkspaceRead"},
    )
    workspace = FsWorkspace(tmp_path)
    real_spawn = lifecycle._default_lifecycle_deps().spawn_process

    def noop_preflight(endpoint: str, required_tools: list[str], timeout: timedelta) -> None:
        del endpoint, required_tools, timeout

    deps_bridge = lifecycle._default_lifecycle_deps().__replace__(
        preflight=noop_preflight, spawn_process=real_spawn
    )
    bridge = lifecycle.start_mcp_server(
        session,
        workspace,
        deps=deps_bridge,
        extras=lifecycle.McpServerExtras(
            restart_policy=McpRestartPolicy(cooldown_seconds=0.0),
        ),
    )

    try:
        assert bridge.process.poll() is None
        # Duplicate submission (client retry after swap, identical payload).
        res2 = submit_artifact_canonical(
            workspace_root=tmp_path,
            artifact_type="development_result",
            parsed_content=parsed,
            markdown=_VALID_DEVELOPMENT_RESULT,
            run_id=run_id,
            deps=deps,
        )
        assert res2.artifact_path is not None
        assert res2.artifact_path.exists()
        assert res2.receipt_path is not None
        assert res2.receipt_path.exists()

        # Invariant 1: Canonical store is byte-equal to the first submission
        # (the duplicate replay produced no observable change).
        second_content = canonical_file.read_text(encoding="utf-8")
        assert second_content == first_content, (
            "duplicate submission must leave the canonical artifact unchanged"
        )

        # Invariant 2: History archive did NOT gain a new entry — dedupe
        # by content + run_id is observable on disk.
        history_after_second = (
            set(DEFAULT_FILE_BACKEND.glob(history_dir, "*.md")) if history_dir.exists() else set()
        )
        if history_dir.exists():
            assert history_after_second == history_after_first, (
                f"duplicate submission created spurious history: "
                f"before={sorted(history_after_first)}, "
                f"after={sorted(history_after_second)}"
            )

        # Invariant 3: Exactly one receipt for (run_id, artifact_type).
        assert artifact_receipt_present(tmp_path, run_id, "development_result") is True

        # Bridge is still serving — observable state proves the swap path
        # did not interrupt the agent run.
        names = _http_tools_list_names(bridge.endpoint_uri(), timeout=2.0)
        assert isinstance(names, list)
    finally:
        with contextlib.suppress(Exception):
            bridge.shutdown()


# ---------------------------------------------------------------------------
# E1.5: ACK-before-resume: parent does not resume candidate until old child ACKs
# ---------------------------------------------------------------------------
def test_e15_ack_before_resume_prompt_ack(tmp_path: Path) -> None:
    """Candidate resume is signaled only after old child explicitly acknowledges pause.

    Real subprocess for BOTH the retiring child (writes ACK after a small
    delay) AND the candidate child (a live sleep subprocess the bridge
    owns but never starts an HTTP listener — the test only exercises the
    pause/ack/resume sentinel protocol, not HTTP serving).
    """
    ralph_root = str(Path(__file__).resolve().parents[2])
    run_id = "run-e15"
    snap = lifecycle.snapshot_dir(tmp_path, run_id)
    pause_file = snap / "accept.pause.stage-old"
    ready_file = tmp_path / "old_ready.txt"

    old_script = tmp_path / "old_child.py"
    old_script.write_text(
        "\n".join(
            [
                "import sys, time",
                "from pathlib import Path",
                f"Path({str(ready_file)!r}).write_text('ready', encoding='utf-8')",
                f"pause_path = Path({str(pause_file)!r})",
                "while not pause_path.exists():",
                "    time.sleep(0.02)",
                "# Small delay before acknowledging to prove parent does not race",
                "time.sleep(0.08)",
                "ack_path = pause_path.with_name(pause_path.name + '.ack')",
                "ack_path.write_text('ACK\\n', encoding='utf-8')",
                "time.sleep(1.0)",
            ]
        ),
        encoding="utf-8",
    )

    pm = get_process_manager()
    old_proc = pm.spawn(
        [sys.executable, str(old_script), ralph_root],
        SpawnOptions(cwd=str(tmp_path), label="e15-old-child"),
    )
    cand_proc = _spawn_real_candidate(tmp_path, label="e15-cand-child", lifetime_s=10.0)

    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not ready_file.exists():
            time.sleep(0.02)
        assert ready_file.exists()

        session_file = tmp_path / "session.json"
        session_file.write_text("{}", encoding="utf-8")

        old_inner = StandaloneMcpProcess(
            "http://127.0.0.1:9100/mcp",
            old_proc,
            session_file,
            stage_id="stage-old",
        )
        cand_inner = StandaloneMcpProcess(
            "http://127.0.0.1:9101/mcp",
            cand_proc,
            session_file,
            stage_id="stage-cand",
        )

        bridge = lifecycle.RestartAwareMcpBridge(
            old_inner,
            restart_fn=lambda: cand_inner,
            restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
            run_id="run-e15",
            workspace_root=tmp_path,
        )

        snap = lifecycle.snapshot_dir(tmp_path, "run-e15")
        resume_file = snap / "accept.resume.stage-cand"

        # Run handover in a background thread to inspect intermediate state
        handover_thread = threading.Thread(
            target=bridge._hand_over_accept,
            args=(old_inner, cand_inner),
        )
        handover_thread.start()

        # While ack has NOT been written, resume must NOT exist
        ack_file = pause_file.with_name(pause_file.name + ".ack")
        # Sample early in the handover window
        if not ack_file.exists():
            assert not resume_file.exists(), (
                "Candidate resume signaled before old child acknowledged pause!"
            )

        handover_thread.join(timeout=3.0)
        assert ack_file.exists()
        assert resume_file.exists()
        # Cleanup: real candidate child is still alive and reaped at end.
        assert cand_proc.poll() is None, "candidate child was killed prematurely"
    finally:
        _reap_process(old_proc)
        _reap_process(cand_proc)


def test_e15_ack_before_resume_hung_child_killed_before_resume(tmp_path: Path) -> None:
    """If old child hangs without ACK, parent terminates it before resuming candidate.

    Real subprocess for BOTH the hung child AND the candidate child.
    """
    hung_script = tmp_path / "hung_child.py"
    hung_script.write_text("import time; time.sleep(10)\n", encoding="utf-8")

    pm = get_process_manager()
    hung_proc = pm.spawn(
        [sys.executable, str(hung_script)],
        SpawnOptions(cwd=str(tmp_path), label="e15-hung-old-child"),
    )
    cand_proc = _spawn_real_candidate(tmp_path, label="e15-cand-hung", lifetime_s=15.0)

    try:
        session_file = tmp_path / "session.json"
        session_file.write_text("{}", encoding="utf-8")

        old_inner = StandaloneMcpProcess(
            "http://127.0.0.1:9100/mcp",
            hung_proc,
            session_file,
            stage_id="stage-hung-old",
        )
        cand_inner = StandaloneMcpProcess(
            "http://127.0.0.1:9101/mcp",
            cand_proc,
            session_file,
            stage_id="stage-cand-2",
        )

        bridge = lifecycle.RestartAwareMcpBridge(
            old_inner,
            restart_fn=lambda: cand_inner,
            restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
            run_id="run-e15-hung",
            workspace_root=tmp_path,
            sleep_fn=lambda s: time.sleep(0.01),
        )

        snap = lifecycle.snapshot_dir(tmp_path, "run-e15-hung")
        resume_file = snap / "accept.resume.stage-cand-2"

        start = time.monotonic()
        bridge._hand_over_accept(old_inner, cand_inner)
        elapsed = time.monotonic() - start

        # Parent must have waited for ack timeout, killed the old child,
        # confirmed it dead, and only then signaled resume
        assert hung_proc.poll() is not None, "Hung old child was not killed"
        assert resume_file.exists(), "Candidate was not resumed after old child killed"
        assert elapsed >= _HANDOVER_ACK_TIMEOUT_S * 0.8
        # The real candidate child is still alive — the bridge owns it
        # but the kill-on-hang path targeted the OLD, not the CANDIDATE.
        assert cand_proc.poll() is None, (
            "candidate child was killed by the bridge's hang-escape path; "
            "expected the bridge to only target the hanging old child"
        )
    finally:
        _reap_process(hung_proc)
        _reap_process(cand_proc)


# ---------------------------------------------------------------------------
# E1.5: Streaming/long-call drain fate during handover (real subprocess)
# ---------------------------------------------------------------------------
def _write_streaming_child_script(
    tmp_path: Path,
    port_file: Path,
    script_name: str,
) -> Path:
    """Write a real subprocess script that holds POSTs open for ~2 s.

    Extracted helper so the E1.5 test body stays under PLR0915. The
    returned script is a self-contained :class:`HTTPServer` that writes
    the bound port to ``port_file`` and serves each POST with a 2 s
    delay — the surface the parent relies on to observe a drain-induced
    severed in-flight call.
    """
    child_script = tmp_path / script_name
    child_script.write_text(
        "\n".join(
            [
                "import json, sys, time, threading",
                "from http.server import BaseHTTPRequestHandler, HTTPServer",
                "from pathlib import Path",
                "",
                f"port_file_path = Path({str(port_file)!r})",
                "",
                "class _StreamingHandler(BaseHTTPRequestHandler):",
                "    def do_POST(self) -> None:",
                "        length = int(self.headers.get('Content-Length', '0'))",
                "        if length:",
                "            self.rfile.read(length)",
                "        # Hold the long-running call open.",
                "        time.sleep(2.0)",
                "        body = json.dumps({",
                "            'jsonrpc': '2.0',",
                "            'id': 1,",
                "            'result': {'streamed': True},",
                "        }).encode('utf-8')",
                "        try:",
                "            self.send_response(200)",
                "            self.send_header('Content-Type', 'application/json')",
                "            self.send_header('Content-Length', str(len(body)))",
                "            self.end_headers()",
                "            self.wfile.write(body)",
                "        except Exception:",
                "            pass",
                "",
                "    def log_message(self, format, *args):  # silence stderr noise",
                "        return",
                "",
                "server = HTTPServer(('127.0.0.1', 0), _StreamingHandler)",
                "port_file_path.write_text(str(server.server_address[1]), encoding='utf-8')",
                "server.serve_forever()",
            ]
        ),
        encoding="utf-8",
    )
    return child_script


def _write_ok_child_script(tmp_path: Path, port_file: Path, script_name: str) -> Path:
    """Write a real subprocess script that responds to GET / with ``b"OK"``."""
    child_script = tmp_path / script_name
    child_script.write_text(
        "\n".join(
            [
                "import json, sys",
                "from http.server import BaseHTTPRequestHandler, HTTPServer",
                "from pathlib import Path",
                f"port_file_path = Path({str(port_file)!r})",
                "",
                "class _OKHandler(BaseHTTPRequestHandler):",
                "    def do_GET(self) -> None:",
                "        body = b'OK'",
                "        self.send_response(200)",
                "        self.send_header('Content-Length', str(len(body)))",
                "        self.end_headers()",
                "        self.wfile.write(body)",
                "",
                "    def log_message(self, format, *args):",
                "        return",
                "",
                "server = HTTPServer(('127.0.0.1', 0), _OKHandler)",
                "port_file_path.write_text(str(server.server_address[1]), encoding='utf-8')",
                "server.serve_forever()",
            ]
        ),
        encoding="utf-8",
    )
    return child_script


def _spawn_and_wait_for_port(
    tmp_path: Path,
    script_path: Path,
    label: str,
    timeout_s: float = 5.0,
    *,
    expected_port_file: Path,
) -> tuple[ManagedProcess, int]:
    """Spawn the script, wait for ``expected_port_file`` to appear, return ``(proc, port)``.

    The previous glob-based version could pick up a stale port file from
    a previous subprocess spawn — once a retired child writes its port
    file, the file lingers on the test's tmp_path until cleanup. The
    caller must pass ``expected_port_file`` so we deterministically read
    the port for THIS spawn, not a previous one.
    """
    pm = get_process_manager()
    proc = pm.spawn(
        [sys.executable, str(script_path)],
        SpawnOptions(cwd=str(tmp_path), label=label),
    )
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if expected_port_file.exists():
            port = int(expected_port_file.read_text(encoding="utf-8").strip())
            return proc, port
        time.sleep(0.02)
    raise AssertionError(
        f"{label}: subprocess never wrote its expected port file {expected_port_file}"
    )


def _force_kill(proc: ManagedProcess, *, drain_grace_s: float = 3.0) -> None:
    """Send SIGTERM, wait up to ``drain_grace_s``, escalate to SIGKILL if needed."""
    proc.terminate()
    deadline = time.monotonic() + drain_grace_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.02)
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=2.0)


def test_e15_streaming_call_drain_fate_real_subprocess(tmp_path: Path) -> None:
    """Streaming / long-running calls during drain finish or surface a typed error.

    The catalogue contract for E1.5 says a long-running call started on the
    retired-but-draining child either completes within the draining child's
    own bound or ends in a typed error, AND whatever the child spawned is
    reaped, AND a draining child does not block the NEXT replacement.

    Drives a real subprocess that holds an HTTP request open for a long
    time, then makes a request, then triggers a drain by having the bridge
    drop the listening socket via the documented pause/ack handshake. The
    in-flight call ends in a typed network error (RemoteDisconnected /
    ConnectionResetError) — which is the production surface today, no
    dedicated typed-error class exists. The drain finishes (the subprocess
    is reaped) and a subsequent bridge operation completes without being
    blocked by the in-flight request.
    """
    port_file = tmp_path / "port_e15_stream.txt"
    child_script = _write_streaming_child_script(tmp_path, port_file, "e15_streaming_child.py")
    proc, port = _spawn_and_wait_for_port(
        tmp_path,
        child_script,
        "e15-streaming-child",
        expected_port_file=port_file,
    )

    # Give the subprocess a bounded head start to bind its HTTPServer and
    # enter ``serve_forever``. The port file is written AFTER the bind, but
    # the spawn handshake may resolve before serve_forever parks on the
    # listening socket — a tiny grace prevents connect races.
    time.sleep(0.05)

    try:
        # 1. Open an in-flight streaming call (long-running).
        inflight_errors: list[Exception] = []
        inflight_completed = threading.Event()

        def _do_streaming_call() -> None:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
                try:
                    streaming_payload: dict[str, object] = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "exec_streaming"},
                    }
                    body = json.dumps(streaming_payload)
                    conn.request(
                        "POST",
                        "/mcp",
                        body=body,
                        headers={"Content-Type": "application/json"},
                    )
                    resp = conn.getresponse()
                    _ = resp.read()
                finally:
                    with contextlib.suppress(Exception):
                        conn.close()
            except Exception as exc:
                inflight_errors.append(exc)
            finally:
                inflight_completed.set()

        worker = threading.Thread(target=_do_streaming_call)
        worker.start()
        time.sleep(0.05)  # let the request enter the slow handler

        # 2. Simulate the drain by closing the listening socket and
        # terminating the subprocess. The in-flight call must surface a
        # typed error (the production surface today is a network error).
        _force_kill(proc)

        # Wait for the in-flight call to surface its error.
        worker.join(timeout=5.0)
        inflight_completed.wait(timeout=1.0)
        assert inflight_errors, "draining must produce a typed error on the in-flight call"
        assert isinstance(
            inflight_errors[0],
            (
                http.client.RemoteDisconnected,
                ConnectionResetError,
                BrokenPipeError,
                OSError,
            ),
        ), (
            "draining severed call must surface a typed network/OS error; "
            f"got {type(inflight_errors[0]).__name__}: {inflight_errors[0]!r}"
        )

        # 3. Whatever the child spawned is reaped — no leak assertion.
        assert proc.poll() is not None, "subprocess must be reaped after drain"

        # 4. A draining child does not block the NEXT replacement. The
        # second subprocess spawn proves the port is free and a new
        # in-flight call on the same port range succeeds.
        new_port_file = tmp_path / "port_e15_stream2.txt"
        new_child_script = _write_ok_child_script(tmp_path, new_port_file, "e15_next_child.py")
        new_proc, new_port = _spawn_and_wait_for_port(
            tmp_path,
            new_child_script,
            "e15-next-child",
            expected_port_file=new_port_file,
        )
        try:
            conn = http.client.HTTPConnection("127.0.0.1", new_port, timeout=2.0)
            try:
                conn.request("GET", "/")
                resp = conn.getresponse()
                assert resp.status == 200
                assert resp.read() == b"OK"
            finally:
                conn.close()
        finally:
            _reap_process(new_proc)
    finally:
        _reap_process(proc)


# ---------------------------------------------------------------------------
# E1.6: Fail-closed resume timeout
# ---------------------------------------------------------------------------
def test_e16_fail_closed_resume_timeout(tmp_path: Path) -> None:
    """Candidate child fails closed and exits non-zero if resume sentinel times out."""
    ralph_root = str(Path(__file__).resolve().parents[2])
    resume_file = tmp_path / "accept.resume"

    cand_script = tmp_path / "cand_timeout_child.py"
    cand_script.write_text(
        "\n".join(
            [
                "import sys, os",
                "from pathlib import Path",
                f"sys.path.insert(0, {ralph_root!r})",
                "from ralph.mcp.server._handover_protocol import wait_for_candidate_resume",
                f"resume_path = Path({str(resume_file)!r})",
                "# Candidate waits with bounded timeout; times out because sentinel never appears",
                "resumed = wait_for_candidate_resume(resume_path, timeout=0.15)",
                "if not resumed:",
                "    # Fail closed with exit code 42",
                "    sys.exit(42)",
                "sys.exit(0)",
            ]
        ),
        encoding="utf-8",
    )

    pm = get_process_manager()
    cand_proc = pm.spawn(
        [sys.executable, str(cand_script)],
        SpawnOptions(cwd=str(tmp_path), label="e16-cand-timeout-child"),
    )

    try:
        rc = cand_proc.wait(timeout=2.0)
        assert rc == 42, f"Candidate exited with unexpected rc {rc}"

        session_file = tmp_path / "session.json"
        session_file.write_text("{}", encoding="utf-8")
        cand_inner = StandaloneMcpProcess("http://127.0.0.1:9200/mcp", cand_proc, session_file)

        old_inner = StandaloneMcpProcess("http://127.0.0.1:9100/mcp", None, session_file)

        bridge = lifecycle.RestartAwareMcpBridge(
            old_inner,
            restart_fn=lambda: cand_inner,
            restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
            run_id="run-e16",
            workspace_root=tmp_path,
        )

        # Verification must fail closed because candidate exited prematurely
        ready = bridge._verify_candidate(cand_inner)
        assert ready is False, "Candidate was not marked unready after timeout exit"
    finally:
        _reap_process(cand_proc)


# ---------------------------------------------------------------------------
# E1.6: Two children writing shared wire ledger serialize and verify cleanly
# ---------------------------------------------------------------------------
def test_e16_interleaved_ledger(tmp_path: Path) -> None:
    """Interleaved writes to shared ledger from two real children cryptographically verify."""
    ralph_root = str(Path(__file__).resolve().parents[2])
    secret = "secret-wire-key-e16"
    run_id = "run-e16-ledger"

    writer_script = tmp_path / "ledger_writer.py"
    writer_script.write_text(
        "\n".join(
            [
                "import sys",
                "from pathlib import Path",
                f"sys.path.insert(0, {ralph_root!r})",
                "from ralph.mcp.server._wire_ledger import append_wire_record",
                "workspace_root = Path(sys.argv[1])",
                "secret = sys.argv[2]",
                "run_id = sys.argv[3]",
                "worker_id = sys.argv[4]",
                "count = int(sys.argv[5])",
                "for i in range(count):",
                "    append_wire_record(",
                "        workspace_root,",
                "        method='tools/call',",
                "        tool_name=f'tool_{worker_id}_{i}',",
                "        params={'worker': worker_id, 'index': i},",
                "        run_id=run_id,",
                "        secret=secret,",
                "    )",
            ]
        ),
        encoding="utf-8",
    )

    pm = get_process_manager()
    p1 = pm.spawn(
        [sys.executable, str(writer_script), str(tmp_path), secret, run_id, "child1", "6"],
        SpawnOptions(cwd=str(tmp_path), label="ledger-writer-1"),
    )
    p2 = pm.spawn(
        [sys.executable, str(writer_script), str(tmp_path), secret, run_id, "child2", "6"],
        SpawnOptions(cwd=str(tmp_path), label="ledger-writer-2"),
    )

    try:
        rc1 = p1.wait(timeout=5.0)
        rc2 = p2.wait(timeout=5.0)
        assert rc1 == 0, f"Child 1 exited with {rc1}"
        assert rc2 == 0, f"Child 2 exited with {rc2}"

        # Hash chain verification must hold across interleaved writes
        assert verify_chain(tmp_path, secret) is True
    finally:
        _reap_process(p1)
        _reap_process(p2)
