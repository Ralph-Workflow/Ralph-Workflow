from __future__ import annotations

import pathlib
import tempfile
from datetime import timedelta

import pytest

from ralph.mcp.server import lifecycle
from ralph.process.mcp_supervisor import DEFAULT_INTERVAL, McpSupervisor


class FakeProcess:
    def __init__(self, poll_result: int | None = None) -> None:
        self._poll_result = poll_result
        self.terminated = False

    @property
    def pid(self) -> int:
        return 99999

    def poll(self) -> int | None:
        return self._poll_result

    def terminate(self, grace_period_s: float = 5.0, **_kwargs: object) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int | None:
        return 0

    def kill(self, **_kwargs: object) -> None:
        pass


def _make_bridge_with_process(
    *, max_restarts: int = 3
) -> tuple[lifecycle.RestartAwareMcpBridge, FakeProcess]:
    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    session_file = tmp_dir / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    initial_process = FakeProcess(poll_result=None)
    inner = lifecycle.StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9500/mcp",
        process=initial_process,
        session_file=session_file,
    )

    def restart_fn() -> lifecycle.StandaloneMcpProcess:
        new_session = tmp_dir / "session-restart.json"
        new_session.write_text("{}", encoding="utf-8")
        return lifecycle.StandaloneMcpProcess(
            endpoint="http://127.0.0.1:9500/mcp",
            process=FakeProcess(poll_result=None),
            session_file=new_session,
        )

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=max_restarts, cooldown_seconds=0.0),
        run_id="test-run",
    )
    return bridge, initial_process


def _make_exhausted_bridge(max_restarts: int = 0) -> lifecycle.RestartAwareMcpBridge:
    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    session_file = tmp_dir / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    inner = lifecycle.StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9500/mcp",
        process=FakeProcess(poll_result=1),
        session_file=session_file,
    )
    return lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: inner,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=max_restarts, cooldown_seconds=0.0),
        run_id="test-run",
    )


def test_supervisor_exits_cleanly_when_agent_completes_normally() -> None:
    bridge, _ = _make_bridge_with_process()
    with McpSupervisor(bridge, check_interval=timedelta(milliseconds=50)):
        pass
    assert bridge.restart_count == 0


def test_supervisor_detects_crash_and_restarts_bridge() -> None:
    bridge, initial_process = _make_bridge_with_process()
    restart_recorded: list[int] = []

    supervisor = McpSupervisor(bridge, on_restart=restart_recorded.append)

    supervisor._do_check_once()
    assert bridge.restart_count == 0

    initial_process._poll_result = 1
    supervisor._do_check_once()

    assert bridge.restart_count == 1
    assert restart_recorded == [1]


def test_supervisor_keeps_supervising_across_transient_failed_respawn() -> None:
    td = pathlib.Path(tempfile.mkdtemp())
    sf = td / "session.json"
    sf.write_text("{}", encoding="utf-8")

    restart_attempts: list[int] = []

    def flaky_restart_fn() -> lifecycle.StandaloneMcpProcess:
        restart_attempts.append(len(restart_attempts) + 1)
        if len(restart_attempts) == 1:
            raise RuntimeError("synthetic transient respawn failure")
        new_sf = td / f"session-{len(restart_attempts)}.json"
        new_sf.write_text("{}", encoding="utf-8")
        return lifecycle.StandaloneMcpProcess(
            endpoint="http://127.0.0.1:9510/mcp",
            process=FakeProcess(poll_result=None),
            session_file=new_sf,
        )

    inner = lifecycle.StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9510/mcp",
        process=FakeProcess(poll_result=1),
        session_file=sf,
    )

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=flaky_restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-supervisor-keeps-supervising",
    )

    restart_recorded: list[int] = []
    error_recorded: list[lifecycle.McpServerError] = []

    supervisor = McpSupervisor(
        bridge,
        on_restart=restart_recorded.append,
        on_error=error_recorded.append,
    )

    result = supervisor._do_check_once()
    assert result is False, "C2: a single failed respawn must not report success"
    assert restart_attempts == [1]
    assert error_recorded == [], "C2: a single failed respawn must not call on_error"
    assert restart_recorded == [], "C2: a failed respawn must not count as a restart"
    assert bridge.restart_count == 1

    result = supervisor._do_check_once()
    assert result is True, "C2: the second restart attempt succeeds"
    assert restart_attempts == [1, 2]
    assert error_recorded == []
    assert restart_recorded == [2]
    assert bridge.restart_count == 2

    assert supervisor._mcp_error is None, (
        "C2: a single failed respawn must not store a terminal McpServerError"
    )


def test_supervisor_raises_mcp_error_when_budget_exhausted() -> None:
    bridge = _make_exhausted_bridge(max_restarts=0)
    supervisor = McpSupervisor(bridge, check_interval=timedelta(milliseconds=20))

    with pytest.raises(lifecycle.McpServerError) as exc_info:
        supervisor._do_check_once()

    assert exc_info.value.restart_count == 0
    with pytest.raises(lifecycle.McpServerError), supervisor:
        pass


def test_supervisor_mcp_error_propagates_even_when_agent_also_fails() -> None:
    bridge = _make_exhausted_bridge(max_restarts=0)
    supervisor = McpSupervisor(bridge, check_interval=timedelta(milliseconds=20))

    with pytest.raises(lifecycle.McpServerError):
        supervisor._do_check_once()

    with pytest.raises(lifecycle.McpServerError), supervisor:
        raise RuntimeError("agent failed too")


def test_supervisor_no_restart_when_process_stays_alive() -> None:
    bridge, _ = _make_bridge_with_process()
    on_restart_calls: list[int] = []

    supervisor = McpSupervisor(bridge, on_restart=on_restart_calls.append)

    supervisor._do_check_once()
    supervisor._do_check_once()
    supervisor._do_check_once()

    assert bridge.restart_count == 0
    assert on_restart_calls == []


def test_supervisor_stops_thread_on_exit() -> None:
    bridge, _ = _make_bridge_with_process()
    supervisor = McpSupervisor(bridge, check_interval=timedelta(milliseconds=50))
    with supervisor:
        assert supervisor._thread.is_alive()
    assert not supervisor._thread.is_alive()


def test_supervisor_mid_run_restart_preserves_endpoint() -> None:
    bridge, initial_process = _make_bridge_with_process(max_restarts=3)
    initial_endpoint = bridge.agent_endpoint_uri()

    supervisor = McpSupervisor(bridge)

    supervisor._do_check_once()
    assert bridge.restart_count == 0

    initial_process._poll_result = 1
    supervisor._do_check_once()

    assert bridge.restart_count == 1
    assert bridge.agent_endpoint_uri() == initial_endpoint


def test_supervisor_uses_configured_check_interval() -> None:
    bridge, _ = _make_bridge_with_process()
    custom_interval = timedelta(milliseconds=350)
    supervisor = McpSupervisor(bridge, check_interval=custom_interval)
    assert supervisor._check_interval == custom_interval


def test_supervisor_default_interval_equals_two_seconds() -> None:
    assert timedelta(seconds=2) == DEFAULT_INTERVAL


def test_supervisor_calls_on_error_callback_when_budget_exhausted() -> None:
    bridge = _make_exhausted_bridge(max_restarts=0)
    error_calls: list[lifecycle.McpServerError] = []

    supervisor = McpSupervisor(bridge, on_error=error_calls.append)

    with pytest.raises(lifecycle.McpServerError):
        supervisor._do_check_once()

    assert len(error_calls) == 1
    assert error_calls[0].restart_count == 0


def test_supervisor_restarts_alive_but_probe_failing_bridge() -> None:
    td = pathlib.Path(tempfile.mkdtemp())
    sf = td / "session.json"
    sf.write_text("{}", encoding="utf-8")

    probe_should_fail = [False]
    restart_done = [False]

    def controlled_probe(endpoint: str, timeout: timedelta) -> None:
        del endpoint, timeout
        if probe_should_fail[0] and not restart_done[0]:
            raise Exception("probe timed out")

    initial_process = FakeProcess(poll_result=None)
    inner = lifecycle.StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9510/mcp",
        process=initial_process,
        session_file=sf,
    )

    def restart_fn() -> lifecycle.StandaloneMcpProcess:
        restart_done[0] = True
        new_sf = td / "session-restart.json"
        new_sf.write_text("{}", encoding="utf-8")
        return lifecycle.StandaloneMcpProcess(
            endpoint="http://127.0.0.1:9510/mcp",
            process=FakeProcess(poll_result=None),
            session_file=new_sf,
        )

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=controlled_probe,
        probe_timeout_fn=lambda: timedelta(milliseconds=200),
    )
    restart_recorded: list[int] = []

    supervisor = McpSupervisor(bridge, on_restart=restart_recorded.append)

    supervisor._do_check_once()
    assert bridge.restart_count == 0

    probe_should_fail[0] = True
    supervisor._do_check_once()
    assert bridge.restart_count == 0
    supervisor._do_check_once()
    assert bridge.restart_count == 0
    supervisor._do_check_once()

    assert bridge.restart_count == 1
    assert restart_recorded == [1]
