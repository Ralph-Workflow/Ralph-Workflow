import contextlib
import os
import pathlib
from datetime import timedelta
from unittest.mock import patch

import psutil
import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._handover_protocol import (
    canonical_tool_entries,
    check_candidate_readiness,
    compute_tool_set_hash,
    create_handover_socketpair,
    describe_tool_set_diff,
    start_private_readiness_listener,
)
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._process_like import ProcessLike
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from tests.mcp._lifecycle_tool_hash_seams import (
    TOOLS_BASE,
    TOOLS_DRIFTED,
    _bridge_with_old_child,
    _captured_logs,
    _FakeListeningSocket,
    _FakeMcpServer,
    _FakeProcess,
    _handover_candidate,
    _make_inner,
    _SyntheticResponder,
    register_synthetic_responders,
)


@pytest.fixture(scope="function")
def synthetic_responders(request: pytest.FixtureRequest) -> list[_SyntheticResponder]:
    return register_synthetic_responders(request)


def test_candidate_verification_retains_tool_hash_on_success(
    tmp_path: pathlib.Path,
    synthetic_responders: list[_SyntheticResponder],
) -> None:
    tools = [{"name": "read_file", "description": "Read file", "inputSchema": {}}]
    expected_hash = compute_tool_set_hash(tools)
    bridge = lifecycle.RestartAwareMcpBridge(
        _make_inner(poll_result=None, pid=1001),
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
        tool_entries_fn=lambda endpoint, timeout: list(tools),
    )
    bridge.capture_tool_hash_baseline()
    candidate = _handover_candidate(
        tmp_path,
        tools=tools,
        pid=2002,
        stage_id="test-stage-1",
        label="s1",
        responders=synthetic_responders,
    )

    ok = bridge._verify_candidate(candidate)
    assert ok is True
    assert bridge.diagnostics().tool_set_hash == expected_hash


def test_tool_set_drift_refused_when_old_child_alive(
    tmp_path: pathlib.Path,
    synthetic_responders: list[_SyntheticResponder],
) -> None:
    tools_old = [{"name": "read_file", "description": "Read file", "inputSchema": {}}]
    tools_new = [{"name": "other_tool", "description": "Other", "inputSchema": {}}]
    bridge = lifecycle.RestartAwareMcpBridge(
        _make_inner(poll_result=None, pid=1001),
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
        tool_entries_fn=lambda endpoint, timeout: list(tools_old),
    )
    bridge.capture_tool_hash_baseline()
    candidate = _handover_candidate(
        tmp_path,
        tools=tools_new,
        pid=2002,
        stage_id="test-stage-drift",
        label="sd",
        responders=synthetic_responders,
    )

    with _captured_logs() as records:
        ok = bridge._verify_candidate(candidate)
    assert ok is False
    drift_logs = [record for record in records if "tool-set drift" in record.lower()]
    assert drift_logs, records
    assert any("other_tool" in record for record in drift_logs)


def test_tool_set_drift_promoted_when_old_child_dead(
    tmp_path: pathlib.Path,
    synthetic_responders: list[_SyntheticResponder],
) -> None:
    tools_old = [{"name": "read_file", "description": "Read file", "inputSchema": {}}]
    tools_new = [{"name": "other_tool", "description": "Other", "inputSchema": {}}]
    bridge = lifecycle.RestartAwareMcpBridge(
        _make_inner(poll_result=1, pid=1001),
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
        tool_entries_fn=lambda endpoint, timeout: list(tools_old),
    )
    bridge.capture_tool_hash_baseline()
    new_hash = compute_tool_set_hash(tools_new)
    candidate = _handover_candidate(
        tmp_path,
        tools=tools_new,
        pid=2002,
        stage_id="test-stage-dead",
        label="sdead",
        responders=synthetic_responders,
    )

    ok = bridge._verify_candidate(candidate)
    assert ok is True
    assert bridge.diagnostics().tool_set_hash == new_hash


def _probe_bridge(
    probe_fn: object,
    *,
    contended: bool,
    clock_fn: object = None,
) -> lifecycle.RestartAwareMcpBridge:
    kwargs: dict[str, object] = {}
    if clock_fn is not None:
        kwargs["clock_fn"] = clock_fn
    return lifecycle.RestartAwareMcpBridge(
        _make_inner(poll_result=None, pid=1001),
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=probe_fn,
        contention_signal_producer=lambda: contended,
        **kwargs,
    )


def _timing_out_probe(endpoint: str, timeout: timedelta) -> None:
    raise TimeoutError("probe timed out")


def _reset_probe(endpoint: str, timeout: timedelta) -> None:
    raise ConnectionResetError("Connection reset by peer")


def _malformed_probe(endpoint: str, timeout: timedelta) -> None:
    raise ValueError("Invalid JSON-RPC response")


def test_probe_timeout_deferred_under_fresh_contention() -> None:
    bridge = _probe_bridge(_timing_out_probe, contended=True)
    with patch.object(bridge, "_perform_restart") as mock_restart:
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is False
        assert mock_restart.call_count == 0
        assert bridge.diagnostics().deferred_probe_failures == 1


def test_probe_connection_reset_not_deferred_under_contention() -> None:
    bridge = _probe_bridge(_reset_probe, contended=True)
    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        assert bridge.diagnostics().deferred_probe_failures == 0
        assert mock_restart.call_count == 1


def test_probe_reset_plus_overrun_not_deferred() -> None:
    clock = [1000.0]

    def reset_with_delay(endpoint: str, timeout: timedelta) -> None:
        clock[0] += 15.0
        raise ConnectionResetError("Connection reset after delay")

    bridge = _probe_bridge(reset_with_delay, contended=True, clock_fn=lambda: clock[0])
    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        assert bridge.diagnostics().deferred_probe_failures == 0
        assert mock_restart.call_count == 1


def test_probe_malformed_response_not_deferred() -> None:
    bridge = _probe_bridge(_malformed_probe, contended=True)
    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        assert bridge.diagnostics().deferred_probe_failures == 0
        assert mock_restart.call_count == 1


def test_confirm_before_replace_defers_on_confirm_success() -> None:
    call_count = 0

    def probe_fn(endpoint: str, timeout: timedelta) -> None:
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            raise TimeoutError("ordinary probe timed out")

    bridge = _probe_bridge(probe_fn, contended=False)
    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is False
        assert mock_restart.call_count == 0
        assert bridge.diagnostics().deferred_probe_failures == 1


def test_confirm_before_replace_replaces_on_confirm_failure() -> None:
    bridge = _probe_bridge(_timing_out_probe, contended=False)
    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        assert mock_restart.call_count == 1


def test_confirm_skipped_when_deferral_ceiling_reached() -> None:
    bridge = _probe_bridge(_timing_out_probe, contended=True)
    ceiling = bridge.diagnostics().probe_deferral_count_ceiling
    for _ in range(ceiling):
        assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.diagnostics().deferred_probe_failures == ceiling

    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        assert mock_restart.call_count == 1


def test_startup_diagnostic_suffix_emits_child_wrote_no_log_output_when_empty(
    tmp_path: pathlib.Path,
) -> None:
    suffix = lifecycle.startup_diagnostic_suffix(tmp_path, 0)
    assert "child wrote no log output" in suffix


def test_mcp_server_error_psutil_status_and_handover_step() -> None:
    err = McpServerError(
        "server failed",
        restart_count=1,
        handover_step="spawn_candidate",
        psutil_status="sleeping",
        child_pid=12345,
    )
    assert err.handover_step == "spawn_candidate"
    assert err.psutil_status == "sleeping"
    err_str = str(err)
    assert "handover_step=spawn_candidate" in err_str
    assert "psutil_status=sleeping" in err_str


def _bridge_with_exhausted_restart_budget(
    tmp_path: pathlib.Path,
) -> lifecycle.RestartAwareMcpBridge:
    inner = _make_inner(
        poll_result=None,
        pid=os.getpid(),
        session_file=tmp_path / "old-session.json",
    )
    return lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=0, cooldown_seconds=0.0),
        run_id="test-run",
        sleep_fn=lambda _seconds: None,
    )


def test_budget_exhausted_error_carries_psutil_status_of_live_child(tmp_path: pathlib.Path) -> None:
    bridge = _bridge_with_exhausted_restart_budget(tmp_path)

    with pytest.raises(McpServerError) as excinfo:
        bridge._perform_restart(process_exited=False, probe_duration_s=0.25)

    err = excinfo.value
    assert err.child_pid == os.getpid()
    assert err.psutil_status == psutil.Process(os.getpid()).status()
    assert f"psutil_status={err.psutil_status}" in str(err)


def test_tool_registry_cap_error_carries_psutil_status_of_live_child(
    tmp_path: pathlib.Path,
) -> None:
    bridge = _bridge_with_old_child(tmp_path, old_pid=os.getpid())
    cap = bridge.diagnostics().tool_registry_max_resets

    for _ in range(cap):
        with contextlib.suppress(McpServerError, OSError):
            bridge.reset_tool_registry()

    with pytest.raises(McpServerError) as excinfo:
        bridge.reset_tool_registry()

    assert "tool-registry-reset exhausted" in str(excinfo.value)
    assert excinfo.value.child_pid == os.getpid()
    assert excinfo.value.psutil_status == psutil.Process(os.getpid()).status()


def test_initial_tool_hash_timeout_is_bounded() -> None:
    bridge = lifecycle.RestartAwareMcpBridge(
        _make_inner(poll_result=None, pid=1001),
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
    )
    diagnostics = bridge.diagnostics()
    assert (
        0.0
        < diagnostics.initial_tool_hash_timeout_s
        < (diagnostics.initial_tool_hash_timeout_max_s)
    )


def test_capture_tool_hash_baseline_stores_served_hash(tmp_path: pathlib.Path) -> None:
    seen: list[tuple[str, float]] = []

    def _fetch(endpoint: str, timeout: float) -> list[dict[str, object]]:
        seen.append((endpoint, timeout))
        return TOOLS_BASE

    bridge = _bridge_with_old_child(tmp_path, old_pid=1001, tool_entries_fn=_fetch)
    bridge.capture_tool_hash_baseline()

    assert bridge.diagnostics().tool_set_hash == compute_tool_set_hash(TOOLS_BASE)
    assert list(bridge.diagnostics().tool_set_entries) == TOOLS_BASE
    assert seen == [
        (
            "http://127.0.0.1:9100/mcp",
            bridge.diagnostics().initial_tool_hash_timeout_s,
        )
    ]


def test_baseline_capture_failure_blocks_candidate_promotion_when_old_child_alive(
    tmp_path: pathlib.Path,
    synthetic_responders: list[_SyntheticResponder],
) -> None:

    def _boom(endpoint: str, timeout: float) -> list[dict[str, object]]:
        raise TimeoutError(f"tools/list unavailable at {endpoint}")

    bridge = _bridge_with_old_child(tmp_path, old_pid=1001, tool_entries_fn=_boom)
    old_process_before = bridge.process
    old_inner_pid_before = bridge.process.pid

    with _captured_logs() as records:
        bridge.capture_tool_hash_baseline()

    assert bridge.diagnostics().tool_set_hash is None
    assert bridge.diagnostics().tool_set_entries == ()
    assert bridge.diagnostics().tool_set_baseline_failed is True
    assert any("tool-hash baseline" in record for record in records)

    candidate = _handover_candidate(
        tmp_path,
        tools=TOOLS_DRIFTED,
        pid=2002,
        stage_id="cand-fail-closed",
        label="failclosed",
        responders=synthetic_responders,
    )

    with _captured_logs() as refuse_records:
        ok = bridge._verify_candidate_socket(candidate, candidate.handover_parent_sock, 2002)

    assert ok is False
    assert any(
        "baseline unavailable" in record.lower() or "tool-hash baseline" in record.lower()
        for record in refuse_records
    )
    assert bridge.process is old_process_before
    assert bridge.process.pid == old_inner_pid_before
    assert bridge.restart_count == 0


def test_baseline_unavailable_promotes_candidate_when_old_child_dead(
    tmp_path: pathlib.Path,
    synthetic_responders: list[_SyntheticResponder],
) -> None:

    def _boom(endpoint: str, timeout: float) -> list[dict[str, object]]:
        raise TimeoutError(f"tools/list unavailable at {endpoint}")

    bridge = lifecycle.RestartAwareMcpBridge(
        _make_inner(poll_result=1, pid=1001),
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
        tool_entries_fn=_boom,
        sleep_fn=lambda _seconds: None,
    )

    bridge.capture_tool_hash_baseline()

    assert bridge.diagnostics().tool_set_baseline_failed is True

    candidate = _handover_candidate(
        tmp_path,
        tools=TOOLS_DRIFTED,
        pid=2003,
        stage_id="cand-dead-escape",
        label="deadescape",
        responders=synthetic_responders,
    )

    with _captured_logs() as promote_records:
        ok = bridge._verify_candidate_socket(candidate, candidate.handover_parent_sock, 2003)

    assert ok is True
    assert any(
        "dead-child escape" in record.lower()
        or ("old child is dead" in record.lower() and "promoting" in record.lower())
        or ("baseline" in record.lower() and "dead" in record.lower())
        for record in promote_records
    )
    assert bridge.diagnostics().tool_set_baseline_failed is False


def test_start_mcp_server_seeds_baseline_from_served_endpoint(tmp_path: pathlib.Path) -> None:
    fetch_calls: list[str] = []
    spawn_calls: list[list[str]] = []
    session = AgentSession(
        session_id="session-baseline",
        run_id="run-baseline",
        drain="planning",
        capabilities={"WorkspaceRead"},
    )

    def _fake_fetch(endpoint: str, timeout: float) -> list[dict[str, object]]:
        fetch_calls.append(endpoint)
        return TOOLS_BASE

    def _fake_spawn(
        command: list[str],
        cwd: pathlib.Path,
        env: dict[str, str],
        *,
        phase: str | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> ProcessLike:
        del cwd, env, phase, pass_fds
        spawn_calls.append(command)
        return _FakeProcess(poll_result=None, pid=os.getpid())

    deps = lifecycle.LifecycleDeps(
        reserve_listening_socket=lambda: ("127.0.0.1", 39411, _FakeListeningSocket()),
        create_session_file=lambda _root, _sess: tmp_path / "baseline-session.json",
        subprocess_env=lambda session_file: {"SESSION": str(session_file)},
        spawn_process=_fake_spawn,
        preflight=lambda endpoint, tools, timeout: None,
        preflight_timeout=lambda: timedelta(seconds=5),
    )

    with patch.object(lifecycle, "served_tool_entries", _fake_fetch):
        bridge = lifecycle.start_mcp_server(session, lifecycle.FsWorkspace(tmp_path), deps=deps)

    assert len(spawn_calls) == 1
    assert fetch_calls == ["http://127.0.0.1:39411/mcp"]
    assert bridge.diagnostics().tool_set_hash == compute_tool_set_hash(TOOLS_BASE)
    bridge.shutdown()


def test_private_channel_hash_equals_shared_endpoint_served_hash() -> None:
    served: list[dict[str, object]] = [
        {"name": "read_file", "description": "Read a file", "inputSchema": {"type": "object"}},
        {"name": "mcp__ralph__read_file", "description": "Read a file", "inputSchema": {}},
        {"name": "write_file", "description": "Write a file", "inputSchema": {}},
    ]
    parent_sock, child_sock = create_handover_socketpair()
    start_private_readiness_listener(
        child_sock,
        _FakeMcpServer(served),
        stage_id="stage-eq",
        session_id="",
        broker_fingerprint=None,
    )

    result = check_candidate_readiness(
        parent_sock,
        expected_spawn_id="stage-eq",
        expected_pid=os.getpid(),
        expected_session_id="",
        expected_broker_fingerprint=None,
        expected_tool_hash=None,
    )
    parent_sock.close()

    assert result.ok is True
    assert list(result.tools) == canonical_tool_entries(served)
    assert result.tool_hash == compute_tool_set_hash(canonical_tool_entries(served))


def test_canonical_tool_entries_collapse_non_canonical_variants() -> None:
    served = [
        {"name": "read_file", "description": "Read", "inputSchema": {}, "annotations": {"x": 1}},
        {"name": "write_file", "inputSchema": {"type": "object"}, "title": "ignored"},
    ]
    canonical = canonical_tool_entries(served)
    assert canonical == [
        {"name": "read_file", "description": "Read", "inputSchema": {}},
        {"name": "write_file", "description": "", "inputSchema": {"type": "object"}},
    ]
    assert canonical_tool_entries(canonical) == canonical


def test_tool_set_drift_logs_tool_diff_and_counts_failed_attempt(
    tmp_path: pathlib.Path,
    synthetic_responders: list[_SyntheticResponder],
) -> None:
    candidates: list[StandaloneMcpProcess] = []

    def _spawn_candidate() -> StandaloneMcpProcess:
        candidate = _handover_candidate(
            tmp_path,
            tools=TOOLS_DRIFTED,
            pid=2002,
            stage_id="cand-drift",
            label="drift",
            responders=synthetic_responders,
        )
        candidates.append(candidate)
        return candidate

    bridge = _bridge_with_old_child(
        tmp_path,
        old_pid=1001,
        restart_fn=_spawn_candidate,
        tool_entries_fn=lambda endpoint, timeout: list(TOOLS_BASE),
    )
    bridge.capture_tool_hash_baseline()

    with _captured_logs() as records:
        replaced = bridge._perform_restart(process_exited=False, probe_duration_s=0.1)

    assert replaced is False
    assert bridge.restart_count == 1
    assert bridge.process.pid == 1001
    assert len(candidates) == 1
    assert candidates[0].process.poll() is not None
    drift_logs = [record for record in records if "tool-set drift" in record.lower()]
    assert drift_logs, records
    assert any("other_tool" in record for record in drift_logs)
    assert any("write_file" in record for record in drift_logs)


def test_validated_candidate_hash_replaces_baseline_for_next_candidate(
    tmp_path: pathlib.Path,
    synthetic_responders: list[_SyntheticResponder],
) -> None:
    bridge = _bridge_with_old_child(
        tmp_path, old_pid=1001, tool_entries_fn=lambda endpoint, timeout: list(TOOLS_BASE)
    )
    bridge.capture_tool_hash_baseline()

    matching = _handover_candidate(
        tmp_path,
        tools=TOOLS_BASE,
        pid=2002,
        stage_id="cand-a",
        label="a",
        responders=synthetic_responders,
    )
    verified_matching = bridge._verify_candidate(matching)
    assert verified_matching is True
    assert bridge.diagnostics().tool_set_hash == compute_tool_set_hash(TOOLS_BASE)

    drifted = _handover_candidate(
        tmp_path,
        tools=TOOLS_DRIFTED,
        pid=2003,
        stage_id="cand-b",
        label="b",
        responders=synthetic_responders,
    )
    verified_drifted = bridge._verify_candidate(drifted)
    assert verified_drifted is False
    assert bridge.diagnostics().tool_set_hash == compute_tool_set_hash(TOOLS_BASE)


def test_describe_tool_set_diff_reports_added_removed_and_changed() -> None:
    expected = [
        {"name": "read_file", "description": "Read", "inputSchema": {}},
        {"name": "gone", "description": "Gone", "inputSchema": {}},
        {"name": "changed", "description": "Before", "inputSchema": {}},
    ]
    candidate = [
        {"name": "read_file", "description": "Read", "inputSchema": {}},
        {"name": "added", "description": "Added", "inputSchema": {}},
        {"name": "changed", "description": "After", "inputSchema": {}},
    ]
    described = describe_tool_set_diff(expected, candidate)
    assert "added=['added']" in described
    assert "removed=['gone']" in described
    assert "changed=['changed']" in described
