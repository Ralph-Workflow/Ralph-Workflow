"""WU-U3a: Handover & In-Flight Edge Cases with real child subprocesses.

Pins catalogue requirements:
- E1.3: Pause/drain stops accepting new calls while in-flight calls finish.
- E1.4: In-flight non-idempotent call severed mid-flight produces typed failure
        (outcome unknown).
- E1.5: ACK-before-resume: parent does not resume candidate until old child
        explicitly ACKs pause or drains.
- E1.6: Fail-closed resume timeout: if candidate resume handshake times out,
        fail closed and mark candidate unready.
- E1.6: Two real children writing to shared on-disk wire ledger interleave
        cleanly and cryptographically verify afterwards.
"""

from __future__ import annotations

import contextlib
import http.client
import os
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.artifacts.markdown import specs as _markdown_specs
from ralph.mcp.protocol.env import MCP_ACCEPT_PAUSE_FILE
from ralph.mcp.server import lifecycle
from ralph.mcp.server._handover_protocol import (
    _HANDOVER_ACK_TIMEOUT_S,
    request_old_child_pause,
    wait_for_old_child_pause_ack,
)
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.server._wire_ledger import verify_chain
from ralph.process.manager import SpawnOptions, get_process_manager

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
# E1.4: In-flight non-idempotent call severed produces typed failure
# ---------------------------------------------------------------------------
def test_e14_outcome_unknown(tmp_path: Path) -> None:
    """Severing connection mid-flight yields typed network error (outcome unknown)."""
    ralph_root = str(Path(__file__).resolve().parents[2])
    port_file = tmp_path / "port_e14.txt"

    child_script = tmp_path / "e14_child.py"
    child_script.write_text(
        "\n".join(
            [
                "import sys, time, threading",
                "from pathlib import Path",
                "from types import SimpleNamespace",
                f"sys.path.insert(0, {ralph_root!r})",
                "from ralph.mcp.server._fallback_standalone_server import _FallbackStandaloneServer",
                "",
                "class _FakeSession:",
                "    session_id = 'e14-session'",
                "",
                "class _FakeServer:",
                "    _session = _FakeSession()",
                "",
                "srv = _FallbackStandaloneServer('127.0.0.1', 0, _FakeServer())",
                "httpd = srv.prepare_httpd()",
                "",
                "def slow_probe():",
                "    time.sleep(2.0)",
                "    return SimpleNamespace(healthy=True, latency_ms=2000.0, reason=None)",
                "",
                "httpd.health_probe_fn = slow_probe",
                f"Path({str(port_file)!r}).write_text(str(srv.bound_address[1]), encoding='utf-8')",
                "ready = threading.Event()",
                "srv.run(ready_event=ready)",
            ]
        ),
        encoding="utf-8",
    )

    pm = get_process_manager()
    proc = pm.spawn(
        [sys.executable, str(child_script)],
        SpawnOptions(cwd=str(tmp_path), label="e14-crash-child"),
    )

    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not port_file.exists():
            time.sleep(0.02)
        assert port_file.exists()
        port = int(port_file.read_text(encoding="utf-8").strip())

        severed_error: list[Exception] = []

        def _do_request() -> None:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
            try:
                conn.request("GET", "/health")
                conn.getresponse()
            except Exception as exc:
                severed_error.append(exc)
            finally:
                conn.close()

        t = threading.Thread(target=_do_request)
        t.start()
        time.sleep(0.1)

        # Abruptly kill the child while request is in-flight
        proc.kill()
        proc.wait(timeout=2.0)

        t.join(timeout=3.0)
        assert len(severed_error) == 1
        exc = severed_error[0]
        # In-flight disconnection surfaces typed network disconnection error
        assert isinstance(
            exc,
            (
                http.client.RemoteDisconnected,
                ConnectionResetError,
                BrokenPipeError,
                OSError,
            ),
        )
    finally:
        _reap_process(proc)


# ---------------------------------------------------------------------------
# E1.5: ACK-before-resume: parent does not resume candidate until old child ACKs
# ---------------------------------------------------------------------------
def test_e15_ack_before_resume_prompt_ack(tmp_path: Path) -> None:
    """Candidate resume is signaled only after old child explicitly acknowledges pause."""
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

        class _CandidateProc:
            pid = 8888

            def poll(self) -> int | None:
                return None

        cand_inner = StandaloneMcpProcess(
            "http://127.0.0.1:9101/mcp",
            _CandidateProc(),
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
    finally:
        _reap_process(old_proc)


def test_e15_ack_before_resume_hung_child_killed_before_resume(tmp_path: Path) -> None:
    """If old child hangs without ACK, parent terminates it before resuming candidate."""
    hung_script = tmp_path / "hung_child.py"
    hung_script.write_text("import time; time.sleep(10)\n", encoding="utf-8")

    pm = get_process_manager()
    hung_proc = pm.spawn(
        [sys.executable, str(hung_script)],
        SpawnOptions(cwd=str(tmp_path), label="e15-hung-old-child"),
    )

    try:
        session_file = tmp_path / "session.json"
        session_file.write_text("{}", encoding="utf-8")

        old_inner = StandaloneMcpProcess(
            "http://127.0.0.1:9100/mcp",
            hung_proc,
            session_file,
            stage_id="stage-hung-old",
        )

        class _CandidateProc:
            pid = 7777

            def poll(self) -> int | None:
                return None

        cand_inner = StandaloneMcpProcess(
            "http://127.0.0.1:9101/mcp",
            _CandidateProc(),
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
    finally:
        _reap_process(hung_proc)


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
