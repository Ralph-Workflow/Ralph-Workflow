from __future__ import annotations

import contextlib
import hashlib
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
_CHANGED_AMBIENT_BROKER_SECRET = "changed-broker-secret"
_SESSION_BROKER_SECRET = "session-broker-secret"
_PROBE_TARGET = "ralph.mcp.server.lifecycle.probe_mcp_http_endpoint"

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_candidate_inheriting_the_ambient_broker_secret_is_promoted": (
        "the bridge now pins the effective broker fingerprint captured from the initial "
        "child's spawn environment and compares every replacement against that pinned "
        "identity; the previous expectation asserted promotion of a candidate whose "
        "broker identity was never compared because the baseline was absent and the "
        "comparison was skipped, so the check now asserts the captured baseline and the "
        "promotion that follows from a matching fingerprint"
    ),
    "test_candidate_inheriting_a_changed_ambient_broker_secret_is_refused": (
        "new coverage for the repaired handover contract: a replacement child that "
        "inherits a changed ambient broker secret must be refused against the pinned "
        "baseline captured at the initial spawn"
    ),
}


def _unreachable_probe(endpoint: str, timeout: timedelta) -> None:
    del endpoint, timeout
    raise ConnectionResetError("probe connection reset")


def _broker_fingerprint(secret: str) -> str:
    return hashlib.sha256(secret[:8].encode()).hexdigest()


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
        pinned_broker_fingerprint = bridge.diagnostics().broker_fingerprint
        assert pinned_broker_fingerprint == _broker_fingerprint(_AMBIENT_BROKER_SECRET), (
            "the bridge must pin the broker identity the initial child used: the initial "
            "child inherited the ambient broker secret, so the pinned baseline is that "
            f"secret's fingerprint, got {pinned_broker_fingerprint!r}"
        )
        old_pid = bridge.process.pid
        new_pid = _drive_failed_probe_restarts(bridge)
        assert bridge.restart_count == 1, "the failing probe must trigger exactly one restart"
        assert new_pid != old_pid, (
            "a candidate that inherits the unchanged ambient broker secret must be "
            "promoted: the broker fingerprint it reports equals the pinned baseline"
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


def test_candidate_inheriting_a_changed_ambient_broker_secret_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = _start_bridge(tmp_path, monkeypatch, session_broker_secret=None)
    try:
        old_pid = bridge.process.pid
        monkeypatch.setenv("RALPH_BROKER_SECRET", _CHANGED_AMBIENT_BROKER_SECRET)
        new_pid = _drive_failed_probe_restarts(bridge)
        assert bridge.restart_count == 1, "the failing probe must trigger exactly one restart"
        assert new_pid == old_pid, (
            "a replacement child that inherits a changed ambient broker secret must never "
            "be promoted: the bridge pins the broker identity the initial child actually "
            "used, and a replacement under a different identity is refused"
        )
    finally:
        with contextlib.suppress(Exception):
            bridge.shutdown()
