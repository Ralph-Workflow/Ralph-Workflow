"""Regression tests for MCP supervision surviving a loaded machine.

Observed 2026-07-25: a run stalled permanently after this sequence.

1. A probe missed its bounded window on a heavily loaded host, so the
   supervisor restarted an MCP server that was slow, not dead — twice,
   in the middle of agent calls.
2. The third spawn's preflight failed and raised a raw
   ``PermanentPreflightError`` instead of ``McpServerError``.
3. ``McpSupervisor._run`` only handled ``McpServerError``, so the
   supervisor thread died. With nothing left probing or restarting the
   server, the pipeline hung until the idle watchdog fired.

Each step is pinned below: a restart needs repeated evidence, a spawn
failure is always typed, and the supervisor keeps supervising no matter
what a health check raises.
"""

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
    """Minimal ProcessLike whose liveness the test controls."""

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

    def wait(self, timeout: float | None = None) -> int | None:
        return 0

    def kill(self) -> None:
        pass


def _make_bridge(
    *,
    probe_outcomes: list[bool],
    process_poll: int | None = None,
) -> tuple[lifecycle.RestartAwareMcpBridge, list[int]]:
    """Return a bridge plus a restart-count log.

    ``probe_outcomes`` is consumed one entry per health check: True means
    the probe succeeded, False means it raised. Exhausting the list keeps
    returning the final outcome.
    """
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
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=10),
        run_id="test-run",
        probe_fn=probe_fn,
        probe_timeout_fn=lambda: timedelta(seconds=5),
    )
    return bridge, restarts


def test_one_missed_probe_does_not_restart_a_running_server() -> None:
    """A slow server under load must not be torn down mid-call."""
    bridge, restarts = _make_bridge(probe_outcomes=[False])

    assert bridge.check_health_and_restart_if_needed() is False
    assert restarts == []


def test_a_recovered_probe_clears_the_failure_streak() -> None:
    """Intermittent misses never accumulate into a restart."""
    bridge, restarts = _make_bridge(probe_outcomes=[False, False, True, False, False])

    for _ in range(5):
        bridge.check_health_and_restart_if_needed()

    assert restarts == []


def test_sustained_probe_failure_still_restarts_the_server() -> None:
    """A genuinely wedged server is still recovered."""
    bridge, restarts = _make_bridge(probe_outcomes=[False])

    results = [bridge.check_health_and_restart_if_needed() for _ in range(3)]

    assert results == [False, False, True]
    assert restarts == [1]


def test_an_exited_process_restarts_without_waiting_for_repeated_evidence() -> None:
    """A dead process is unambiguous, so it is recovered immediately."""
    bridge, restarts = _make_bridge(probe_outcomes=[True], process_poll=1)

    assert bridge.check_health_and_restart_if_needed() is True
    assert restarts == [1]


def test_preflight_failure_on_a_live_process_surfaces_as_mcp_server_error(
    tmp_path: Path,
) -> None:
    """A spawn that cannot become ready reports the pipeline's own error type.

    A raw preflight error escaped every ``McpServerError`` handler on the
    way out, including the supervisor's, and killed the supervisor thread.
    """

    def failing_preflight(endpoint: str, required_tools: list[str], timeout: timedelta) -> None:
        msg = "HTTP MCP request failed with status '503': transport_loop_detected"
        raise RuntimeError(msg)

    deps = lifecycle.LifecycleDeps(
        reserve_port=lambda: 43123,
        create_session_file=lambda root, session: tmp_path / "session.json",
        subprocess_env=lambda session_file: {},
        spawn_process=lambda command, cwd, env, *, phase=None: FakeProcess(poll_result=None),
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
    """A restart that raises must not silently end supervision.

    A dead supervisor stops probing and restarting entirely, which is how
    the observed run hung: the server was never recovered again, so the
    pipeline sat idle until the watchdog fired.
    """
    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    session_file = tmp_dir / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    second_restart_reached = threading.Event()
    attempts: list[int] = []

    def always_failing_probe(endpoint: str, timeout: timedelta) -> None:
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
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=10),
        run_id="test-run",
        probe_fn=always_failing_probe,
        probe_timeout_fn=lambda: timedelta(seconds=5),
    )

    with McpSupervisor(bridge, check_interval=timedelta(seconds=0.01)):
        reached = second_restart_reached.wait(timeout=10.0)

    assert reached, "supervision stopped after the first restart raised"
    assert bridge.restart_count == 1
