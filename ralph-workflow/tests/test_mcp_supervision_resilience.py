from __future__ import annotations

import pathlib
import tempfile
import threading
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.process.mcp_supervisor import McpSupervisor

if TYPE_CHECKING:
    from pathlib import Path

PREFLIGHT_TIMEOUT = 7.0


class FakeProcess:
    def __init__(self, poll_result: int | None = None) -> None:
        self._poll_result = poll_result
        self.terminated = False

    @property
    def pid(self) -> int:
        return 99999

    def poll(self) -> int | None:
        return self._poll_result

    def terminate(self, grace_period_s: float = 5.0) -> None:
        self.terminated = True
        self._poll_result = 0

    def wait(self, timeout: float | None = None) -> int | None:
        return 0

    def kill(self) -> None:
        pass


def _make_bridge(
    *,
    probe_outcomes: list[bool],
    process_poll: int | None = None,
    max_restarts: int = 10,
) -> tuple[lifecycle.RestartAwareMcpBridge, list[int]]:
    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    session_file = tmp_dir / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    restarts: list[int] = []
    remaining = list(probe_outcomes)

    def probe_fn(endpoint: str, timeout: timedelta) -> None:
        succeeded = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if not succeeded:
            msg = f"probe timed out after {timeout}"
            raise TimeoutError(msg)

    def restart_fn() -> lifecycle.StandaloneMcpProcess:
        restarts.append(len(restarts) + 1)
        new_session = tmp_dir / f"session-{len(restarts)}.json"
        new_session.write_text("{}", encoding="utf-8")
        return lifecycle.StandaloneMcpProcess(
            endpoint="http://127.0.0.1:9500/mcp",
            process=FakeProcess(poll_result=None),
            session_file=new_session,
        )

    bridge = lifecycle.RestartAwareMcpBridge(
        lifecycle.StandaloneMcpProcess(
            endpoint="http://127.0.0.1:9500/mcp",
            process=FakeProcess(poll_result=process_poll),
            session_file=session_file,
        ),
        restart_fn=restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=max_restarts, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=probe_fn,
        probe_timeout_fn=lambda: timedelta(seconds=5),
    )
    return bridge, restarts


def test_one_missed_probe_does_not_restart_a_running_server() -> None:
    bridge, restarts = _make_bridge(probe_outcomes=[False])

    assert bridge.check_health_and_restart_if_needed() is False
    assert restarts == []


def test_a_recovered_probe_clears_the_failure_streak() -> None:
    bridge, restarts = _make_bridge(probe_outcomes=[False, False, True, True, True, False, False])

    for _ in range(7):
        bridge.check_health_and_restart_if_needed()

    assert restarts == []


def test_sustained_probe_failure_still_restarts_the_server() -> None:
    bridge, restarts = _make_bridge(probe_outcomes=[False])

    results = [bridge.check_health_and_restart_if_needed() for _ in range(3)]

    assert results == [False, False, True]
    assert restarts == [1]


def test_an_exited_process_restarts_without_waiting_for_repeated_evidence() -> None:
    bridge, restarts = _make_bridge(probe_outcomes=[True], process_poll=1)

    assert bridge.check_health_and_restart_if_needed() is True
    assert restarts == [1]


def test_exhausted_budget_interrupts_a_blocked_invocation() -> None:
    bridge, restarts = _make_bridge(probe_outcomes=[True], process_poll=1, max_restarts=0)
    interrupted = threading.Event()

    supervisor = McpSupervisor(
        bridge,
        check_interval=timedelta(milliseconds=10),
        on_error=lambda _exc: interrupted.set(),
    )

    with pytest.raises(lifecycle.McpServerError, match="budget"), supervisor:
        assert interrupted.wait(timeout=5.0), "supervisor never interrupted the blocked invocation"

    assert restarts == []


def test_supervision_interrupts_the_agent_by_default() -> None:
    bridge, _ = _make_bridge(probe_outcomes=[True], process_poll=1, max_restarts=0)
    killed: list[str] = []

    supervisor = McpSupervisor(
        bridge,
        check_interval=timedelta(milliseconds=10),
        agent_label_scope="run-owned-by-this-bridge",
        terminate_agents=killed.append,
    )

    with pytest.raises(lifecycle.McpServerError, match="budget"), supervisor:
        deadline = 5.0
        step = 0.01
        waited = 0.0
        while not killed and waited < deadline:
            threading.Event().wait(step)
            waited += step

    assert killed == ["invoke:run-owned-by-this-bridge:"]


def test_preflight_failure_on_a_live_process_surfaces_as_mcp_server_error(
    tmp_path: Path,
) -> None:

    def failing_preflight(endpoint: str, required_tools: list[str], timeout: timedelta) -> None:
        msg = "HTTP MCP request failed with status '503': transport_loop_detected"
        raise RuntimeError(msg)

    deps = lifecycle.LifecycleDeps(
        create_session_file=lambda root, session: tmp_path / "session.json",
        subprocess_env=lambda session_file: {},
        spawn_process=lambda command, cwd, env, *, phase=None, pass_fds=(): FakeProcess(
            poll_result=None
        ),
        preflight=failing_preflight,
        preflight_timeout=lambda: timedelta(seconds=PREFLIGHT_TIMEOUT),
    )
    session = AgentSession(
        session_id="session-1",
        run_id="run-1",
        drain="planning",
        capabilities={"WorkspaceRead", "ArtifactSubmit"},
    )

    with pytest.raises(lifecycle.McpServerError, match="did not become ready"):
        lifecycle.start_mcp_server(session, lifecycle.FsWorkspace(tmp_path), deps=deps)


def test_supervisor_keeps_supervising_after_a_failed_restart() -> None:
    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    session_file = tmp_dir / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    second_restart_reached = threading.Event()
    attempts: list[int] = []

    def always_failing_probe(endpoint: str, timeout: timedelta) -> None:
        del endpoint, timeout
        if second_restart_reached.is_set():
            return
        msg = "probe timed out"
        raise TimeoutError(msg)

    def restart_fn() -> lifecycle.StandaloneMcpProcess:
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            msg = "preflight failed while the process was still alive"
            raise RuntimeError(msg)
        second_restart_reached.set()
        new_session = tmp_dir / f"session-{len(attempts)}.json"
        new_session.write_text("{}", encoding="utf-8")
        return lifecycle.StandaloneMcpProcess(
            endpoint="http://127.0.0.1:9500/mcp",
            process=FakeProcess(poll_result=None),
            session_file=new_session,
        )

    bridge = lifecycle.RestartAwareMcpBridge(
        lifecycle.StandaloneMcpProcess(
            endpoint="http://127.0.0.1:9500/mcp",
            process=FakeProcess(poll_result=None),
            session_file=session_file,
        ),
        restart_fn=restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=10, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=always_failing_probe,
        probe_timeout_fn=lambda: timedelta(seconds=5),
    )

    bridge.check_health_and_restart_if_needed()
    bridge.check_health_and_restart_if_needed()
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.restart_count == 1
    assert attempts == [1]
    result = bridge.check_health_and_restart_if_needed()
    assert result is True, (
        f"second restart attempt should succeed; restart_count={bridge.restart_count}"
    )
    assert bridge.restart_count == 2
    assert second_restart_reached.is_set()
