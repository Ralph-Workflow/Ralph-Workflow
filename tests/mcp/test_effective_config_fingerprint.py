class _DeadProcess:
    pid = 4103

    def poll(self) -> int | None:
        return 0

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


class _NoToolsServer:
    def list_tools(self) -> list[dict[str, object]]:
        return []


class _LiveProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        raise AssertionError("old child must never be terminated on a refusal")

    def kill(self) -> None:
        raise AssertionError("old child must never be killed on a refusal")


class _LiveInner:
    """Minimal serving ``_inner`` double whose process is alive."""

    def __init__(self, pid: int) -> None:
        self.process = _LiveProcess(pid)
        self.endpoint = f"http://127.0.0.1:{9000 + pid}/mcp"

    def shutdown(self) -> None:
        raise AssertionError("old child must never be shut down on a refusal")


def _bridge_with_old_child(run_id: str) -> tuple[lifecycle.RestartAwareMcpBridge, _LiveInner]:
    old_inner = _LiveInner(4101)
    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: _LiveInner(4102),
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id=run_id,
    )
    bridge._effective_config_fingerprint = "a" * 64
    return bridge, old_inner


def _candidate_double(pid: int) -> Any:
    cand = Any.__new__(lifecycle.StandaloneMcpProcess)
    cand.stage_id = "stage-candidate"
    cand.handover_parent_sock = None
    cand.process = _LiveProcess(pid)
    return cand


def _refuse_readiness(monkeypatch: pytest.MonkeyPatch, stage: str) -> None:
    def _fake_check(*_args: object, **_kwargs: object) -> CandidateReadiness:
        return CandidateReadiness(ok=False, failure_stage=stage, detail=f"refused at {stage}")

    monkeypatch.setattr(lifecycle, "check_candidate_readiness", _fake_check)


def test_c1_config_fingerprint_mismatch_refuses_promotion_and_keeps_old_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D4/C1 step 2: a diverged candidate configuration must NOT be promoted.

    Drives the real ``RestartAwareMcpBridge._verify_candidate_socket`` seam and
    asserts the observable contract: promotion is refused and the old child
    stays installed as ``_inner``, never shut down or signalled.
    """
    _refuse_readiness(monkeypatch, "effective_config_fingerprint_mismatch")
    bridge, old_inner = _bridge_with_old_child("cfg-refusal-run")
    parent_sock, child_sock = socket.socketpair()
    try:
        verified = bridge._verify_candidate_socket(_candidate_double(4202), parent_sock, 4202)
    finally:
        child_sock.close()
    assert verified is False, "a diverged candidate configuration must never be promoted"
    assert bridge._inner is old_inner, "the old child must remain the serving inner"


def test_c1_config_fingerprint_mismatch_promotes_only_when_old_child_is_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The single legal escape: with no old child left, promotion stays possible.

    Without this, a permanently diverged configuration would wedge a bridge
    whose old child is already gone and cost the agent its MCP server.
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
        verified = bridge._verify_candidate_socket(_candidate_double(4203), parent_sock, 4203)
    finally:
        child_sock.close()
    assert verified is True, "with no old child to preserve, promotion must remain possible"


def test_candidate_reports_parent_bound_config_fingerprint_over_readiness_channel() -> None:
    """The child must actually TRANSPORT the fingerprint (D4 production seam).

    A previous revision added the readiness field but never fed it, so the
    child raised ``TypeError`` and the whole C1 private preflight silently
    never answered. This drives the real listener with the production-shaped
    call and asserts the digest comes back.
    """
    digest = hashlib.sha256(b"effective-config").hexdigest()
    parent_sock, child_sock = socket.socketpair()
    parent_sock.settimeout(5.0)
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