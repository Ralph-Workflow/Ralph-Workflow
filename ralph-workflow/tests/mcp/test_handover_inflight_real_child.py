from __future__ import annotations

import contextlib
import http.client
import json
import os
import subprocess
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

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]

assert _markdown_specs is not None


def _reap_process(proc: ManagedProcess) -> None:
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


def test_e13_pause_drain_stops_accepting_while_inflight_finishes(tmp_path: Path) -> None:
    ralph_root = str(Path(__file__).resolve().parents[2])
    port_file = tmp_path / "port.txt"
    pause_file = tmp_path / "accept.pause"
    probe_entered_file = tmp_path / "probe.entered"

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
                f"    Path({str(probe_entered_file)!r}).touch()",
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
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline and not port_file.exists():
            time.sleep(0.02)
        assert port_file.exists(), "Child failed to report bound port"
        port = int(port_file.read_text(encoding="utf-8").strip())

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

        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not probe_entered_file.exists():
            time.sleep(0.025)
        assert probe_entered_file.exists(), "In-flight request never reached slow_probe"

        request_old_child_pause(pause_file)

        ack = wait_for_old_child_pause_ack(pause_file, timeout=2.0)
        assert ack is True, "Retiring child failed to acknowledge pause"

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

        req_thread.join(timeout=3.0)
        assert len(inflight_result) == 1, "In-flight request did not complete"
        status, body = inflight_result[0]
        assert status == 200
        assert b"healthy" in body
    finally:
        _reap_process(proc)

    assert proc.poll() is not None


def test_e14_outcome_unknown(tmp_path: Path) -> None:
    port_file = tmp_path / "port_e14.txt"
    handler_entered_file = tmp_path / "e14_handler.entered"

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
                f"        Path({str(handler_entered_file)!r}).touch()",
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
                payload: dict[str, object] = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "submit_artifact", "arguments": {"x": 1}},
                }
                body = json.dumps(payload).encode("utf-8")
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
        _wait_for_marker(handler_entered_file, "non-idempotent request never entered the handler")

        proc.kill()
        proc.wait(timeout=2.0)

        t.join(timeout=3.0)
        assert len(severed_error) == 1, (
            f"in-flight non-idempotent call must surface an error; got {severed_error!r}"
        )
        exc = severed_error[0]

        assert isinstance(exc, OSError), (
            f"non-idempotent severed call must surface a typed OSError-family "
            f"exception; got {type(exc).__name__}: {exc!r}"
        )
    finally:
        _reap_process(proc)

    assert proc.poll() is not None


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
    from datetime import timedelta

    from ralph.mcp.protocol.session import AgentSession as _AgentSession

    spec = get_spec("development_result")
    parsed, parse_diagnostics = parse_and_validate(_VALID_DEVELOPMENT_RESULT, spec)
    assert not any(d.severity == "error" for d in parse_diagnostics)

    run_id = f"run-e14-swap-{uuid.uuid4().hex[:8]}"
    deps = _ArtifactHandlerDeps(history_enabled=True)

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

        second_content = canonical_file.read_text(encoding="utf-8")
        assert second_content == first_content, (
            "duplicate submission must leave the canonical artifact unchanged"
        )

        history_after_second = (
            set(DEFAULT_FILE_BACKEND.glob(history_dir, "*.md")) if history_dir.exists() else set()
        )
        if history_dir.exists():
            assert history_after_second == history_after_first, (
                f"duplicate submission created spurious history: "
                f"before={sorted(history_after_first)}, "
                f"after={sorted(history_after_second)}"
            )

        assert artifact_receipt_present(tmp_path, run_id, "development_result") is True

        names = _http_tools_list_names(bridge.endpoint_uri(), timeout=2.0)
        assert isinstance(names, list)
    finally:
        with contextlib.suppress(Exception):
            bridge.shutdown()


def test_e15_ack_before_resume_prompt_ack(tmp_path: Path) -> None:
    ralph_root = str(Path(__file__).resolve().parents[2])
    run_id = f"run-e15-{uuid.uuid4().hex}"
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
            run_id=run_id,
            workspace_root=tmp_path,
        )

        snap = lifecycle.snapshot_dir(tmp_path, run_id)
        resume_file = snap / "accept.resume.stage-cand"

        handover_thread = threading.Thread(
            target=bridge._hand_over_accept,
            args=(old_inner, cand_inner),
        )
        handover_thread.start()

        ack_file = pause_file.with_name(pause_file.name + ".ack")
        if not ack_file.exists():
            assert not resume_file.exists(), (
                "Candidate resume signaled before old child acknowledged pause!"
            )

        handover_thread.join(timeout=3.0)
        assert ack_file.exists()
        assert resume_file.exists()
        assert cand_proc.poll() is None, "candidate child was killed prematurely"
    finally:
        _reap_process(old_proc)
        _reap_process(cand_proc)


def test_e15_ack_before_resume_hung_child_killed_before_resume(tmp_path: Path) -> None:
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

        hung_run_id = f"run-e15-hung-{uuid.uuid4().hex}"
        bridge = lifecycle.RestartAwareMcpBridge(
            old_inner,
            restart_fn=lambda: cand_inner,
            restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
            run_id=hung_run_id,
            workspace_root=tmp_path,
            sleep_fn=lambda s: time.sleep(0.025),
        )

        snap = lifecycle.snapshot_dir(tmp_path, hung_run_id)
        resume_file = snap / "accept.resume.stage-cand-2"

        start = time.monotonic()
        bridge._hand_over_accept(old_inner, cand_inner)
        elapsed = time.monotonic() - start

        assert hung_proc.poll() is not None, "Hung old child was not killed"
        assert resume_file.exists(), "Candidate was not resumed after old child killed"
        assert elapsed >= _HANDOVER_ACK_TIMEOUT_S * 0.8
        assert cand_proc.poll() is None, (
            "candidate child was killed by the bridge's hang-escape path; "
            "expected the bridge to only target the hanging old child"
        )
    finally:
        _reap_process(hung_proc)
        _reap_process(cand_proc)


def _wait_for_marker(marker: Path, message: str) -> None:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(0.025)
    assert marker.exists(), message


def _write_streaming_child_script(
    tmp_path: Path,
    port_file: Path,
    script_name: str,
    handler_entered_file: Path,
) -> Path:
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
                f"        Path({str(handler_entered_file)!r}).touch()",
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
    port_file = tmp_path / "port_e15_stream.txt"
    handler_entered_file = tmp_path / "e15_handler.entered"
    child_script = _write_streaming_child_script(
        tmp_path, port_file, "e15_streaming_child.py", handler_entered_file
    )
    proc, port = _spawn_and_wait_for_port(
        tmp_path,
        child_script,
        "e15-streaming-child",
        expected_port_file=port_file,
    )

    try:
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
        _wait_for_marker(handler_entered_file, "streaming request never entered the handler")

        _force_kill(proc)

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

        assert proc.poll() is not None, "subprocess must be reaped after drain"

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


def test_e16_fail_closed_resume_timeout(tmp_path: Path) -> None:
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
        rc = cand_proc.wait(timeout=5.0)
        assert rc == 42, f"Candidate exited with unexpected rc {rc}"

        session_file = tmp_path / "session.json"
        session_file.write_text("{}", encoding="utf-8")
        cand_inner = StandaloneMcpProcess("http://127.0.0.1:9200/mcp", cand_proc, session_file)

        old_inner = StandaloneMcpProcess("http://127.0.0.1:9100/mcp", None, session_file)

        bridge = lifecycle.RestartAwareMcpBridge(
            old_inner,
            restart_fn=lambda: cand_inner,
            restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
            run_id=f"run-e16-{uuid.uuid4().hex}",
            workspace_root=tmp_path,
        )

        ready = bridge._verify_candidate(cand_inner)
        assert ready is False, "Candidate was not marked unready after timeout exit"
    finally:
        _reap_process(cand_proc)


def _wait_child_bounded(proc: ManagedProcess, *, timeout_s: float) -> int:
    deadline = time.monotonic() + timeout_s
    while True:
        rc = proc.poll()
        if rc is not None:
            return rc
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            return proc.wait(timeout=min(remaining, 1.0))
        except subprocess.TimeoutExpired:
            pass
    rc = proc.poll()
    if rc is not None:
        return rc
    raise subprocess.TimeoutExpired(proc.record.command, timeout_s)


def test_e16_interleaved_ledger(tmp_path: Path) -> None:
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
                "        lock_timeout_s=5.0,",
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
        overall_deadline = time.monotonic() + 8.0
        remaining_1 = max(1.0, overall_deadline - time.monotonic())
        rc1 = _wait_child_bounded(p1, timeout_s=remaining_1)
        remaining_2 = max(1.0, overall_deadline - time.monotonic())
        rc2 = _wait_child_bounded(p2, timeout_s=remaining_2)
        assert rc1 == 0, f"Child 1 exited with {rc1}"
        assert rc2 == 0, f"Child 2 exited with {rc2}"

        assert verify_chain(tmp_path, secret) is True
    finally:
        _reap_process(p1)
        _reap_process(p2)
