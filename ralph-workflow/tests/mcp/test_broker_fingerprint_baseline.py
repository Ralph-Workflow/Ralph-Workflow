from __future__ import annotations

import contextlib
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import (
    McpRestartPolicy,
    McpServerExtras,
    RestartAwareMcpBridge,
    clear_host_contention_signals,
    start_mcp_server,
)
from ralph.workspace.fs import FsWorkspace

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(60),
]

_PROBE_FAILURES_BEFORE_RESTART = 3
_AMBIENT_BROKER_SECRET = "ambient-broker-secret"
_SESSION_BROKER_SECRET = "session-broker-secret"
_PROBE_TARGET = "ralph.mcp.server.lifecycle.probe_mcp_http_endpoint"


def _unreachable_probe(endpoint: str, timeout: timedelta) -> None:
    del endpoint, timeout
    raise ConnectionResetError("probe connection reset")


def _start_bridge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    session_broker_secret: str | None,
) -> RestartAwareMcpBridge:
    clear_host_contention_signals()
    monkeypatch.setenv("RALPH_BROKER_SECRET", _AMBIENT_BROKER_SECRET)
    monkeypatch.setattr(_PROBE_TARGET, _unreachable_probe)
    session = AgentSession(
        session_id=f"session-fp-{uuid.uuid4().hex[:8]}",
        run_id=f"run-fp-{uuid.uuid4().hex[:8]}",
        drain="development",
        capabilities={"WorkspaceRead"},
        broker_secret=session_broker_secret,
    )
    extras = McpServerExtras(restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0))
    return start_mcp_server(session, FsWorkspace(tmp_path), extras=extras)


def _drive_failed_probe_restarts(bridge: RestartAwareMcpBridge) -> int:
    for _ in range(_PROBE_FAILURES_BEFORE_RESTART):
        bridge.check_health_and_restart_if_needed()
    return bridge.process.pid


def test_candidate_inheriting_the_ambient_broker_secret_is_promoted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _start_bridge(tmp_path, monkeypatch, session_broker_secret=None)
    try:
        old_pid = bridge.process.pid
        new_pid = _drive_failed_probe_restarts(bridge)
        assert bridge.restart_count == 1, "the failing probe must trigger exactly one restart"
        assert new_pid != old_pid, (
            "a candidate that inherits the ambient broker secret must be promoted: the "
            "bridge's broker baseline is the broker identity the candidate inherits, so "
            "the candidate's fingerprint is a baseline match, not a mismatch"
        )
    finally:
        with contextlib.suppress(Exception):
            bridge.shutdown()


def test_candidate_running_under_a_divergent_broker_secret_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _start_bridge(tmp_path, monkeypatch, session_broker_secret=_SESSION_BROKER_SECRET)
    try:
        old_pid = bridge.process.pid
        new_pid = _drive_failed_probe_restarts(bridge)
        assert bridge.restart_count == 1, "the failing probe must trigger exactly one restart"
        assert new_pid == old_pid, (
            "a candidate running under a broker secret the session does not declare must "
            "never be promoted"
        )
    finally:
        with contextlib.suppress(Exception):
            bridge.shutdown()
