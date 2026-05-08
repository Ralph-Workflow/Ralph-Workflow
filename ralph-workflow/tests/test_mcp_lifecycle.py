from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.upstream.client import HttpUpstreamClient
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.registry import UpstreamRegistry

if TYPE_CHECKING:
    from pathlib import Path

PREFLIGHT_TIMEOUT = 123


class FakeProcess:
    def __init__(self, poll_result: int | None = None, pid: int = 12345) -> None:
        self._poll_result = poll_result
        self._pid = pid
        self.terminated = False
        self.terminated_grace_period: float | None = None

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        return self._poll_result

    def terminate(self, grace_period_s: float = 5.0) -> None:
        self.terminated = True
        self.terminated_grace_period = grace_period_s

    def wait(self, timeout: float | None = None) -> int | None:
        return 0

    def kill(self) -> None:
        return None


def test_start_mcp_server_uses_injected_dependencies(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_reserve_port() -> int:
        seen["reserved"] = True
        return 43123

    def fake_create_session_file(root: Path, session: object) -> Path:
        seen["session_root"] = root
        seen["session"] = session
        path = tmp_path / "session.json"
        path.write_text("{}", encoding="utf-8")
        return path

    def fake_subprocess_env(session_file: Path) -> dict[str, str]:
        seen["session_file"] = session_file
        return {"RALPH_MCP_SESSION_FILE": str(session_file)}

    def fake_spawn(
        command: list[str], cwd: Path, env: dict[str, str], *, phase: str | None = None
    ) -> FakeProcess:
        seen["command"] = command
        seen["cwd"] = cwd
        seen["env"] = env
        return FakeProcess(pid=9999)

    def fake_preflight(endpoint: str, required_tools: list[str], timeout: timedelta) -> None:
        seen["endpoint"] = endpoint
        seen["required_tools"] = required_tools
        seen["timeout"] = timeout

    deps = lifecycle.LifecycleDeps(
        reserve_port=fake_reserve_port,
        create_session_file=fake_create_session_file,
        subprocess_env=fake_subprocess_env,
        spawn_process=fake_spawn,
        preflight=fake_preflight,
        preflight_timeout=lambda: timedelta(seconds=PREFLIGHT_TIMEOUT),
    )

    session = AgentSession(
        session_id="session-1",
        run_id="run-1",
        drain="planning",
        capabilities={"WorkspaceRead", "ArtifactSubmit"},
    )
    workspace = lifecycle.FsWorkspace(tmp_path)

    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps)

    assert bridge.agent_endpoint_uri() == "http://127.0.0.1:43123/mcp"
    assert seen["session_root"] == tmp_path
    assert seen["endpoint"] == "http://127.0.0.1:43123/mcp"
    assert seen["timeout"] == timedelta(seconds=PREFLIGHT_TIMEOUT)
    assert seen["cwd"] == tmp_path


def test_start_mcp_server_includes_extra_env_in_subprocess(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_reserve_port() -> int:
        return 43124

    def fake_create_session_file(root: Path, session: object) -> Path:
        path = tmp_path / "session-extra-env.json"
        path.write_text("{}", encoding="utf-8")
        return path

    def fake_subprocess_env(session_file: Path) -> dict[str, str]:
        return {"RALPH_MCP_SESSION_FILE": str(session_file)}

    def fake_spawn(
        command: list[str], cwd: Path, env: dict[str, str], *, phase: str | None = None
    ) -> FakeProcess:
        del command, cwd, phase
        seen["env"] = env
        return FakeProcess()

    def fake_preflight(endpoint: str, required_tools: list[str], timeout: timedelta) -> None:
        del endpoint, required_tools, timeout

    deps = lifecycle.LifecycleDeps(
        reserve_port=fake_reserve_port,
        create_session_file=fake_create_session_file,
        subprocess_env=fake_subprocess_env,
        spawn_process=fake_spawn,
        preflight=fake_preflight,
        preflight_timeout=lambda: timedelta(seconds=5),
    )

    session = AgentSession(
        session_id="session-extra-env",
        run_id="run-extra-env",
        drain="planning",
        capabilities={"WorkspaceRead", "ArtifactSubmit"},
    )
    workspace = lifecycle.FsWorkspace(tmp_path)

    lifecycle.start_mcp_server(
        session,
        workspace,
        deps=deps,
        extra_env={"RALPH_UPSTREAM_MCP_CONFIG": '[{"name":"docs","transport":"http","url":"http://docs"}]'},
    )

    env = cast("dict[str, str]", seen["env"])
    assert env["RALPH_UPSTREAM_MCP_CONFIG"] == (
        '[{"name":"docs","transport":"http","url":"http://docs"}]'
    )



def test_start_mcp_server_preflight_includes_upstream_tool_names(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_reserve_port() -> int:
        return 43125

    def fake_create_session_file(root: Path, session: object) -> Path:
        path = tmp_path / "session-upstream.json"
        path.write_text("{}", encoding="utf-8")
        return path

    def fake_subprocess_env(session_file: Path) -> dict[str, str]:
        return {"RALPH_MCP_SESSION_FILE": str(session_file)}

    def fake_spawn(
        command: list[str], cwd: Path, env: dict[str, str], *, phase: str | None = None
    ) -> FakeProcess:
        return FakeProcess()

    def fake_preflight(endpoint: str, required_tools: list[str], timeout: timedelta) -> None:
        seen["required_tools"] = list(required_tools)

    upstream = UpstreamMcpServer(name="remote", transport="http", url="http://unused")

    def fake_caller(method: str, params: dict[str, object]) -> dict[str, object]:
        if method == "tools/list":
            return {"tools": [{"name": "ping", "description": "Ping", "inputSchema": {}}]}  # type: ignore[return-value]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        return {}

    upstream_registry = UpstreamRegistry.build(
        [upstream],
        client_factory=lambda srv: HttpUpstreamClient(srv, caller=fake_caller),  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    )

    deps = lifecycle.LifecycleDeps(
        reserve_port=fake_reserve_port,
        create_session_file=fake_create_session_file,
        subprocess_env=fake_subprocess_env,
        spawn_process=fake_spawn,
        preflight=fake_preflight,
        preflight_timeout=lambda: timedelta(seconds=5),
    )

    session = AgentSession(
        session_id="session-upstream-lifecycle",
        run_id="run-upstream-lifecycle",
        drain="development",
        capabilities={"WorkspaceRead", "ArtifactSubmit", "UpstreamToolUse"},
    )
    workspace = lifecycle.FsWorkspace(tmp_path)

    lifecycle.start_mcp_server(session, workspace, upstream_registry=upstream_registry, deps=deps)

    required = cast("list[str]", seen["required_tools"])
    assert "ralph_upstream__remote__ping" in required


# ---------------------------------------------------------------------------
# RestartAwareMcpBridge tests
# ---------------------------------------------------------------------------


def _make_standalone(
    endpoint: str = "http://127.0.0.1:9001/mcp", *, poll_result: int | None = None
) -> lifecycle.StandaloneMcpProcess:
    import pathlib  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    session_file = tmp_dir / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    return lifecycle.StandaloneMcpProcess(
        endpoint=endpoint,
        process=FakeProcess(poll_result=poll_result),
        session_file=session_file,
    )


def test_mcp_restart_policy_default_is_1000() -> None:
    policy = lifecycle.McpRestartPolicy()
    assert policy.max_restarts == 1000  # noqa: PLR2004


def test_restart_aware_bridge_returns_false_when_process_alive() -> None:
    inner = _make_standalone(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=_make_standalone,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3),
    )
    result = bridge.check_health_and_restart_if_needed()
    assert result is False
    assert bridge.restart_count == 0


def test_restart_aware_bridge_restarts_dead_process() -> None:
    inner = _make_standalone(endpoint="http://127.0.0.1:9001/mcp", poll_result=1)
    # Restarted process uses the same endpoint — stable endpoint invariant
    new_inner = _make_standalone(endpoint="http://127.0.0.1:9001/mcp", poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: new_inner,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3),
    )
    result = bridge.check_health_and_restart_if_needed()
    assert result is True
    assert bridge.restart_count == 1
    assert bridge.agent_endpoint_uri() == "http://127.0.0.1:9001/mcp"


def test_restart_aware_bridge_raises_when_budget_exhausted() -> None:
    inner = _make_standalone(poll_result=1)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_standalone(poll_result=1),
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=2),
    )
    bridge.check_health_and_restart_if_needed()  # restart 1
    bridge.check_health_and_restart_if_needed()  # restart 2
    with pytest.raises(lifecycle.McpServerError) as exc_info:
        bridge.check_health_and_restart_if_needed()  # budget exhausted
    assert exc_info.value.restart_count == 2  # noqa: PLR2004


def test_restart_aware_bridge_calls_restart_fn_on_each_restart() -> None:
    calls: list[int] = []
    counter = 0

    def counting_restart_fn() -> lifecycle.StandaloneMcpProcess:
        nonlocal counter
        counter += 1
        calls.append(counter)
        return _make_standalone(endpoint=f"http://127.0.0.1:{9000 + counter}/mcp", poll_result=1)

    inner = _make_standalone(poll_result=1)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=counting_restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3),
    )
    bridge.check_health_and_restart_if_needed()
    bridge.check_health_and_restart_if_needed()
    assert calls == [1, 2]


def test_check_mcp_bridge_health_noop_for_non_restart_bridge() -> None:
    """check_mcp_bridge_health is safe to call on bridges that are not RestartAwareMcpBridge."""

    class FakeBridge:
        def start(self) -> None: ...
        def agent_endpoint_uri(self) -> str: return "http://x"
        def endpoint_uri(self) -> str: return "http://x"
        def shutdown(self) -> None: ...

    fake: Any = FakeBridge()
    lifecycle.check_mcp_bridge_health(fake)  # must not raise


def test_standalone_mcp_process_shutdown_removes_session_file_even_if_process_exited(
    tmp_path: Path,
) -> None:
    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    bridge = lifecycle.StandaloneMcpProcess(
        endpoint="http://127.0.0.1:43123/mcp",
        process=FakeProcess(poll_result=0),
        session_file=session_file,
    )

    bridge.shutdown()

    assert session_file.exists() is False


def test_start_mcp_server_restart_fn_runs_preflight(tmp_path: Path) -> None:
    """Restart function from start_mcp_server re-runs preflight on each restart."""
    preflight_calls: list[str] = []
    spawn_call_count = [0]

    def fake_reserve_port() -> int:
        return 43200 + spawn_call_count[0]

    def fake_create_session_file(root: Path, session: object) -> Path:
        del root, session
        path = tmp_path / f"session-{spawn_call_count[0]}.json"
        path.write_text("{}", encoding="utf-8")
        return path

    def fake_subprocess_env(session_file: Path) -> dict[str, str]:
        return {"RALPH_MCP_SESSION_FILE": str(session_file)}

    def fake_spawn(
        command: list[str], cwd: Path, env: dict[str, str], *, phase: str | None = None
    ) -> FakeProcess:
        del command, cwd, env, phase
        spawn_call_count[0] += 1
        return FakeProcess(poll_result=1 if spawn_call_count[0] == 1 else None)

    def fake_preflight(endpoint: str, required_tools: list[str], timeout: object) -> None:
        preflight_calls.append(endpoint)

    deps = lifecycle.LifecycleDeps(
        reserve_port=fake_reserve_port,
        create_session_file=fake_create_session_file,
        subprocess_env=fake_subprocess_env,
        spawn_process=fake_spawn,
        preflight=fake_preflight,
        preflight_timeout=lambda: timedelta(seconds=5),
    )

    session = AgentSession(
        session_id="restart-preflight-test",
        run_id="run-restart-preflight",
        drain="planning",
        capabilities={"WorkspaceRead"},
    )
    workspace = lifecycle.FsWorkspace(tmp_path)

    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps)
    assert len(preflight_calls) == 1, "preflight runs once at initial spawn"

    # Trigger a restart: process is dead (poll_result=1), so health check restarts
    bridge.check_health_and_restart_if_needed()
    assert len(preflight_calls) == 2, "preflight runs again after restart"  # noqa: PLR2004
    assert spawn_call_count[0] == 2, "spawn called twice (initial + restart)"  # noqa: PLR2004


def test_restart_aware_bridge_process_dying_after_initial_preflight(tmp_path: Path) -> None:
    """Simulates process dying after initial preflight; bridge detects and restarts."""
    # Start with a process that is alive (poll=None)
    initial_process = FakeProcess(poll_result=None)
    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    inner = lifecycle.StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9010/mcp",
        process=initial_process,
        session_file=session_file,
    )
    restarted_process = FakeProcess(poll_result=None)
    restarted_session = tmp_path / "restarted-session.json"
    restarted_session.write_text("{}", encoding="utf-8")
    # Same endpoint as initial process — stable port guaranteed by start_mcp_server
    restarted = lifecycle.StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9010/mcp",
        process=restarted_process,
        session_file=restarted_session,
    )

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: restarted,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3),
    )

    # Process is alive — health check returns False
    result = bridge.check_health_and_restart_if_needed()
    assert result is False
    assert bridge.restart_count == 0

    # Now simulate process crash (as if it died after initial preflight)
    initial_process._poll_result = 1

    result = bridge.check_health_and_restart_if_needed()
    assert result is True
    assert bridge.restart_count == 1
    # Endpoint stays stable (same port) even after restart
    assert bridge.agent_endpoint_uri() == "http://127.0.0.1:9010/mcp"


# ---------------------------------------------------------------------------
# Responsiveness probe tests (alive-but-unresponsive scenario)
# ---------------------------------------------------------------------------


def test_restart_aware_bridge_restarts_when_process_alive_but_probe_fails() -> None:
    """Bridge restarts when process is alive (poll=None) but responsiveness probe fails."""
    from datetime import timedelta  # noqa: PLC0415

    inner = _make_standalone(poll_result=None)
    new_inner = _make_standalone(poll_result=None)
    restart_calls: list[int] = []

    def failing_probe(endpoint: str, timeout: timedelta) -> None:
        del endpoint, timeout
        raise Exception("probe timed out")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: (restart_calls.append(1), new_inner)[-1],
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3),
        probe_fn=failing_probe,
        probe_timeout_fn=lambda: timedelta(seconds=1),
    )
    result = bridge.check_health_and_restart_if_needed()
    assert result is True
    assert bridge.restart_count == 1
    assert restart_calls == [1]


def test_restart_aware_bridge_raises_budget_exhausted_on_probe_failure() -> None:
    """McpServerError raised when budget exhausted even though process is alive (probe failure)."""
    from datetime import timedelta  # noqa: PLC0415

    inner = _make_standalone(poll_result=None)

    def failing_probe(endpoint: str, timeout: timedelta) -> None:
        del endpoint, timeout
        raise Exception("connection refused")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_standalone(poll_result=None),
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=0),
        probe_fn=failing_probe,
        probe_timeout_fn=lambda: timedelta(seconds=1),
    )
    with pytest.raises(lifecycle.McpServerError) as exc_info:
        bridge.check_health_and_restart_if_needed()
    assert exc_info.value.restart_count == 0


def test_restart_aware_bridge_terminates_stale_process_on_probe_failure(tmp_path: Path) -> None:
    """Stale but alive process is terminated via shutdown() before respawn on probe failure."""
    from datetime import timedelta  # noqa: PLC0415

    session_file = tmp_path / "probe-stale-session.json"
    session_file.write_text("{}", encoding="utf-8")
    initial_process = FakeProcess(poll_result=None)
    inner = lifecycle.StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9900/mcp",
        process=initial_process,
        session_file=session_file,
    )

    def failing_probe(endpoint: str, timeout: timedelta) -> None:
        del endpoint, timeout
        raise Exception("request timed out")

    new_inner = _make_standalone(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: new_inner,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3),
        probe_fn=failing_probe,
        probe_timeout_fn=lambda: timedelta(seconds=1),
    )
    bridge.check_health_and_restart_if_needed()
    assert initial_process.terminated is True, "stale process must be terminated before respawn"


def test_restart_aware_bridge_no_probe_fn_means_alive_is_healthy() -> None:
    """When probe_fn is None, a live process is always treated as healthy (backward compat)."""
    inner = _make_standalone(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=_make_standalone,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3),
        probe_fn=None,
    )
    result = bridge.check_health_and_restart_if_needed()
    assert result is False
    assert bridge.restart_count == 0


def test_restart_aware_bridge_passing_probe_does_not_trigger_restart() -> None:
    """When probe_fn succeeds, bridge reports healthy and does not restart."""
    from datetime import timedelta  # noqa: PLC0415

    probe_calls: list[str] = []

    def passing_probe(endpoint: str, timeout: timedelta) -> None:
        probe_calls.append(endpoint)

    inner = _make_standalone(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=_make_standalone,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3),
        probe_fn=passing_probe,
        probe_timeout_fn=lambda: timedelta(seconds=1),
    )
    result = bridge.check_health_and_restart_if_needed()
    assert result is False
    assert bridge.restart_count == 0
    assert probe_calls  # probe was called


def test_start_mcp_server_stable_endpoint_across_restarts(tmp_path: Path) -> None:
    """start_mcp_server reuses the same port on every restart so the endpoint stays stable."""
    reserved_ports: list[int] = []
    spawn_call_count = [0]

    def fake_reserve_port() -> int:
        reserved_ports.append(43300)
        return 43300

    def fake_create_session_file(root: Path, session: object) -> Path:
        path = tmp_path / f"session-stable-{spawn_call_count[0]}.json"
        path.write_text("{}", encoding="utf-8")
        return path

    def fake_subprocess_env(session_file: Path) -> dict[str, str]:
        return {"RALPH_MCP_SESSION_FILE": str(session_file)}

    def fake_spawn(
        command: list[str], cwd: Path, env: dict[str, str], *, phase: str | None = None
    ) -> FakeProcess:
        del command, cwd, env, phase
        spawn_call_count[0] += 1
        # First process is dead; second is alive
        return FakeProcess(poll_result=1 if spawn_call_count[0] == 1 else None)

    endpoints_seen: list[str] = []

    def fake_preflight(endpoint: str, required_tools: list[str], timeout: object) -> None:
        endpoints_seen.append(endpoint)

    deps = lifecycle.LifecycleDeps(
        reserve_port=fake_reserve_port,
        create_session_file=fake_create_session_file,
        subprocess_env=fake_subprocess_env,
        spawn_process=fake_spawn,
        preflight=fake_preflight,
        preflight_timeout=lambda: timedelta(seconds=5),
    )

    session = AgentSession(
        session_id="stable-endpoint-test",
        run_id="run-stable",
        drain="planning",
        capabilities={"WorkspaceRead"},
    )
    workspace = lifecycle.FsWorkspace(tmp_path)

    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps)
    initial_endpoint = bridge.agent_endpoint_uri()

    bridge.check_health_and_restart_if_needed()

    # Port reserved exactly once, not on each restart
    assert reserved_ports == [43300], "reserve_port called more than once"
    # Endpoint unchanged after restart
    assert bridge.agent_endpoint_uri() == initial_endpoint
    assert initial_endpoint == "http://127.0.0.1:43300/mcp"
    # Both preflight calls use the same endpoint
    assert endpoints_seen == [initial_endpoint, initial_endpoint]


# ---------------------------------------------------------------------------
# model_identity serialization tests
# ---------------------------------------------------------------------------


def test_session_payload_json_includes_model_identity_when_known() -> None:
    """_session_payload_json serializes model_identity for sessions with a known provider."""
    import json  # noqa: PLC0415

    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity  # noqa: PLC0415
    from ralph.mcp.server.lifecycle import _session_payload_json  # noqa: PLC0415

    session = AgentSession(
        session_id="sid-mi",
        run_id="run-mi",
        drain="development",
        capabilities={"WorkspaceRead"},
        model_identity=MultimodalModelIdentity(
            provider="anthropic", model_id="claude-3-5-sonnet", transport="cli"
        ),
    )
    payload = json.loads(_session_payload_json(session))
    assert "model_identity" in payload
    assert payload["model_identity"]["provider"] == "anthropic"
    assert payload["model_identity"]["model_id"] == "claude-3-5-sonnet"
    assert payload["model_identity"]["transport"] == "cli"


def test_session_payload_json_omits_model_identity_when_unknown() -> None:
    """_session_payload_json omits model_identity for UNKNOWN_IDENTITY sessions."""
    import json  # noqa: PLC0415

    from ralph.mcp.server.lifecycle import _session_payload_json  # noqa: PLC0415

    session = AgentSession(
        session_id="sid-unknown",
        run_id="run-unknown",
        drain="development",
        capabilities={"WorkspaceRead"},
    )
    payload = json.loads(_session_payload_json(session))
    assert "model_identity" not in payload


def test_session_payload_json_omits_model_identity_for_sessions_without_attribute() -> None:
    """_session_payload_json is safe when session lacks model_identity attribute."""
    import json  # noqa: PLC0415

    from ralph.mcp.server.lifecycle import _session_payload_json  # noqa: PLC0415

    class _MinimalSession:
        session_id = "sid-min"
        run_id = "run-min"
        drain = "development"
        capabilities: set[str]

        def __init__(self) -> None:
            self.capabilities = set()

    payload = json.loads(_session_payload_json(_MinimalSession()))  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    assert "model_identity" not in payload
