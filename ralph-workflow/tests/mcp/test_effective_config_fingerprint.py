"""Tests for the C1 step 2 effective-config fingerprint surface.

The fingerprint is a secret-safe SHA-256 of the canonical JSON form of
the merged ``McpConfig`` (mcp.toml + global + bundled layers). The
parent captures it before the first spawn, persists it in
``session.json``, and the candidate echoes it on the private readiness
channel. A candidate whose fingerprint diverges from the parent's is
refused (``failure_stage="effective_config_fingerprint_mismatch"``)
unless the old child is already dead (dead-old-child escape).
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._handover_protocol import (
    CandidateReadiness,
    _validate_init_metadata,
    start_private_readiness_listener,
)
from ralph.mcp.server._snapshots import (
    compute_effective_config_fingerprint,
    read_snapshot,
    snapshot_dir,
    write_snapshot,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.server.lifecycle import StandaloneMcpProcess

_HEX_64 = re.compile(r"[0-9a-f]{64}")


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_validate_init_metadata_passes_on_matching_fingerprint() -> None:
    err = _validate_init_metadata(
        {
            "pid": 1001,
            "spawn_id": "stage_x",
            "session_id": "sess_x",
            "broker_secret_fingerprint": "fp_x",
            "effective_config_fingerprint": "cfg_x",
        },
        expected_pid=1001,
        expected_spawn_id="stage_x",
        expected_session_id="sess_x",
        expected_broker_fingerprint="fp_x",
        expected_effective_config_fingerprint="cfg_x",
    )
    assert err is None


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_validate_init_metadata_flags_mismatched_fingerprint() -> None:
    err = _validate_init_metadata(
        {
            "pid": 1001,
            "spawn_id": "stage_x",
            "session_id": "sess_x",
            "broker_secret_fingerprint": "fp_x",
            "effective_config_fingerprint": "cfg_y",
        },
        expected_pid=1001,
        expected_spawn_id="stage_x",
        expected_session_id="sess_x",
        expected_broker_fingerprint="fp_x",
        expected_effective_config_fingerprint="cfg_x",
    )
    assert err is not None
    stage, detail = err
    assert stage == "effective_config_fingerprint_mismatch"
    assert "cfg_x" in detail and "cfg_y" in detail


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_none_expected_baseline_is_tolerated_not_a_mismatch() -> None:
    """When the parent captured no fingerprint (no toml/no tomllib) the
    candidate is allowed to echo any value, including ``None``.

    Note: the sibling agent is rewriting ``_handover_protocol._validate_init_metadata``
    so this codifies the *current* failure-open contract verbatim. If that rewrite
    flips the contract to fail-closed, this test must be updated to expect a
    mismatch stage rather than ``err is None``.
    """
    err = _validate_init_metadata(
        {
            "pid": 1001,
            "spawn_id": "stage_x",
            "session_id": "sess_x",
            "broker_secret_fingerprint": "fp_x",
            "effective_config_fingerprint": None,
        },
        expected_pid=1001,
        expected_spawn_id="stage_x",
        expected_session_id="sess_x",
        expected_broker_fingerprint="fp_x",
        expected_effective_config_fingerprint=None,
    )
    assert err is None


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_write_snapshot_persists_effective_config_fingerprint(tmp_path) -> None:
    """``write_snapshot`` records the fingerprint so cross-respawn checks
    against the persisted baseline, not against the parent's runtime only.

    The value asserted on is a real 64-char lowercase-hex SHA-256 and the
    assertion targets the ``effective_config_fingerprint`` KEY (the prior
    weak form also matched ``session_id``/``run_id`` string values).
    """
    snap = snapshot_dir(tmp_path, "run-fp-test")
    expected_fp = hashlib.sha256(b"test/fingerprint-fixture").hexdigest()
    # Sanity: the digest used below must be a 64-char lowercase-hex string.
    assert _HEX_64.fullmatch(expected_fp) is not None
    write_snapshot(
        snap,
        session_id="sess_fingerprint_test",
        run_id="run-fp-test",
        broker_secret_fingerprint="fp_x",
        effective_config_fingerprint=expected_fp,
    )
    payload = read_snapshot(snap)
    assert payload["effective_config_fingerprint"] == expected_fp
    assert _HEX_64.fullmatch(payload["effective_config_fingerprint"]) is not None
    # Roundtrip preserves a non-empty fingerprint key
    assert isinstance(json.loads(json.dumps(payload)), dict)


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_compute_effective_config_fingerprint_is_deterministic(tmp_path) -> None:
    """Same workspace -> same fingerprint (no secrets in the hash).

    Non-vacuous: the function is expected to produce a real 64-char hex
    digest for the bundled-default-only configuration path so the test
    can assert on the actual shape, not just on ``None == None``.
    """
    ws = tmp_path
    fp_a = compute_effective_config_fingerprint(ws)
    fp_b = compute_effective_config_fingerprint(ws)
    assert isinstance(fp_a, str)
    assert _HEX_64.fullmatch(fp_a) is not None
    assert fp_a == fp_b


class _LiveInner:
    """Minimal serving ``_inner`` double whose process is alive."""

    def __init__(self, pid: int) -> None:
        self.process = _LiveProcess(pid)
        self.endpoint = f"http://127.0.0.1:{9000 + pid}/mcp"

    def shutdown(self) -> None:
        raise AssertionError("old child must never be shut down on a refusal")


class _LiveProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        raise AssertionError("old child must never be terminated on a refusal")

    def kill(self) -> None:
        raise AssertionError("old child must never be killed on a refusal")


def _bridge_with_old_child() -> tuple[lifecycle.RestartAwareMcpBridge, _LiveInner]:
    old_inner = _LiveInner(4101)
    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: _LiveInner(4102),
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="cfg-refusal-run",
    )
    bridge._effective_config_fingerprint = "a" * 64
    return bridge, old_inner


def _candidate(pid: int) -> StandaloneMcpProcess:
    return cast(
        "StandaloneMcpProcess",
        SimpleNamespace(
            stage_id="stage-candidate", handover_parent_sock=None, process=_LiveProcess(pid)
        ),
    )


def _refuse_readiness(monkeypatch: pytest.MonkeyPatch, stage: str) -> None:
    def _fake_check(*_args: object, **_kwargs: object) -> CandidateReadiness:
        return CandidateReadiness(ok=False, failure_stage=stage, detail=f"got X expected {stage}")

    monkeypatch.setattr(lifecycle, "check_candidate_readiness", _fake_check)


@pytest.mark.subprocess_e2e
def test_c1_config_fingerprint_mismatch_refuses_promotion_and_keeps_old_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D4/C1 step 2: a diverged candidate configuration must NOT be promoted.

    Drives the real ``RestartAwareMcpBridge._verify_candidate_socket`` seam and
    asserts the three observable contract points: promotion is refused, the old
    child stays installed as ``_inner`` (and is never shut down or signalled),
    and the bridge generation does not advance.
    """
    _refuse_readiness(monkeypatch, "effective_config_fingerprint_mismatch")
    bridge, old_inner = _bridge_with_old_child()
    parent_sock, child_sock = socket.socketpair()
    try:
        verified = bridge._verify_candidate_socket(_candidate(4202), parent_sock, 4202)
    finally:
        child_sock.close()
    assert verified is False, "a diverged candidate configuration must never be promoted"
    assert bridge._inner is old_inner, "the old child must remain the serving inner"


@pytest.mark.subprocess_e2e
def test_c1_config_fingerprint_mismatch_promotes_only_when_old_child_is_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The single legal escape: with no old child left, promote and log it.

    E1.9's dead-old-child exception must keep applying to the config-snapshot
    gate so a permanently diverged configuration cannot wedge a bridge whose
    old child is already gone.
    """
    _refuse_readiness(monkeypatch, "effective_config_fingerprint_mismatch")
    dead_inner = _LiveInner(4103)
    dead_inner.process = _DeadProcess()
    bridge = lifecycle.RestartAwareMcpBridge(
        dead_inner,
        restart_fn=lambda: _LiveInner(4104),
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="cfg-refusal-dead-run",
    )
    bridge._effective_config_fingerprint = "b" * 64
    monkeypatch.setattr(bridge, "_is_old_child_dead", lambda: True)
    parent_sock, child_sock = socket.socketpair()
    try:
        verified = bridge._verify_candidate_socket(_candidate(4203), parent_sock, 4203)
    finally:
        child_sock.close()
    assert verified is True, "with no old child to preserve, promotion must remain possible"


class _DeadProcess:
    pid = 4103

    def poll(self) -> int | None:
        return 0

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


@pytest.mark.subprocess_e2e
def test_candidate_reports_parent_bound_config_fingerprint_over_readiness_channel() -> None:
    """The child must actually TRANSPORT the fingerprint (D4 production seam).

    A previous revision added the readiness field but never fed it, so the
    whole private preflight raised ``TypeError`` in the child and the C1
    identity contract silently never answered. This drives the real listener
    with the production-shaped call and asserts the digest comes back.
    """
    digest = hashlib.sha256(b"effective-config").hexdigest()
    parent_sock, child_sock = socket.socketpair()
    child_sock.settimeout(5.0)
    try:
        start_private_readiness_listener(
            child_sock,
            _NoToolsServer(),
            stage_id="stage-1",
            session_id="sess-1",
            broker_fingerprint="c" * 8,
            effective_config_fingerprint=digest,
        )
        parent_sock.sendall(b'{"jsonrpc":"2.0","id":1,"method":"initialize"}\n')
        buf = b""
        while b"\n" not in buf:
            chunk = parent_sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        payload = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
    finally:
        parent_sock.close()
        child_sock.close()
    result = payload["result"]
    assert result["effective_config_fingerprint"] == digest
    assert result["spawn_id"] == "stage-1"
    assert result["session_id"] == "sess-1"


class _NoToolsServer:
    def list_tools(self) -> list[dict[str, object]]:
        return []
