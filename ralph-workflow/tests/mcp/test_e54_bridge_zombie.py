from __future__ import annotations

import os
import threading
from pathlib import Path

import psutil

from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_health_event import (
    RestartDecision,
    RestartReason,
    get_restart_decisions,
)
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess

_ZOMBIE_EXIT_STATUS: int = 17

_ENDPOINT = "http://127.0.0.1:9100/mcp"

_REPLACEMENT_PID: int = 900_001
_SECOND_REPLACEMENT_PID: int = 900_002


def _open_fd_count() -> int:
    try:
        return int(psutil.Process().num_fds())
    except (AttributeError, OSError, psutil.Error):
        pass
    fd_dir = Path("/proc/self/fd")
    try:
        return sum(1 for _ in fd_dir.iterdir()) - 1
    except OSError:
        return 0


def _child_pids() -> frozenset[int]:
    try:
        return frozenset(child.pid for child in psutil.Process().children())
    except (AttributeError, OSError, psutil.Error):
        return frozenset()


def _bare_pid_probe_says_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class _ZombieChild:
    def __init__(self, pid: int, exit_status: int) -> None:
        self._pid = pid
        self._exit_status = exit_status
        self.poll_calls = 0
        self.terminate_calls = 0
        self.kill_calls = 0

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        self.poll_calls += 1
        return self._exit_status

    def wait(self, timeout: float | None = None) -> int:
        return self._exit_status

    def terminate(self, grace_period_s: float = 5.0, **_kwargs: object) -> None:
        self.terminate_calls += 1

    def kill(self, **_kwargs: object) -> None:
        self.kill_calls += 1

    def cleanup_orphans(self) -> None:
        return


class _LiveChild:
    def __init__(self, pid: int) -> None:
        self._pid = pid
        self._exit_status: int | None = None
        self.poll_calls = 0
        self.terminate_calls = 0
        self.kill_calls = 0

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        self.poll_calls += 1
        return self._exit_status

    def wait(self, timeout: float | None = None) -> int | None:
        return self._exit_status

    def terminate(self, grace_period_s: float = 5.0, **_kwargs: object) -> None:
        self.terminate_calls += 1
        self._exit_status = 0

    def kill(self, **_kwargs: object) -> None:
        self.kill_calls += 1
        self._exit_status = -9

    def cleanup_orphans(self) -> None:
        return


class _ReplacementSource:
    def __init__(self, replacement: StandaloneMcpProcess) -> None:
        self._replacement = replacement
        self.calls = 0
        self.make_before_break_requests = 0

    def __call__(self, *, make_before_break: bool = False) -> StandaloneMcpProcess:
        self.calls += 1
        if make_before_break:
            self.make_before_break_requests += 1
        return self._replacement


def _make_inner(
    process: _ZombieChild | _LiveChild,
    session_file: Path,
) -> StandaloneMcpProcess:
    return StandaloneMcpProcess(
        endpoint=_ENDPOINT,
        process=process,
        session_file=session_file,
    )


def _make_bridge(
    inner: StandaloneMcpProcess,
    replacement: StandaloneMcpProcess,
) -> tuple[lifecycle.RestartAwareMcpBridge, _ReplacementSource]:
    source = _ReplacementSource(replacement)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=source,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="e54-zombie-run",
        sleep_fn=lambda _seconds: None,
    )
    return bridge, source


def test_e54_zombie_child_is_treated_as_dead_and_replaced_by_bridge(tmp_path: Path) -> None:
    fds_before = _open_fd_count()
    threads_before = threading.active_count()
    children_before = _child_pids()

    live_pid = os.getpid()
    assert _bare_pid_probe_says_alive(live_pid), (
        "precondition: the bare-PID liveness probe must succeed on this pid, "
        "otherwise the zombie ambiguity this test pins is not reproduced"
    )

    zombie = _ZombieChild(pid=live_pid, exit_status=_ZOMBIE_EXIT_STATUS)
    inner = _make_inner(zombie, tmp_path / "zombie-session.json")
    replacement = _make_inner(
        _LiveChild(pid=_REPLACEMENT_PID),
        tmp_path / "replacement-session.json",
    )
    bridge, source = _make_bridge(inner, replacement)

    newest_before = get_restart_decisions()[-1:]
    try:
        restarted = bridge.check_health_and_restart_if_needed()

        assert restarted is True, "a zombie child must be replaced, not tolerated"
        assert bridge.restart_count == 1
        assert bridge.process.pid == _REPLACEMENT_PID
        assert bridge.endpoint_uri() == _ENDPOINT

        assert zombie.poll_calls > 0
        assert zombie.terminate_calls == 0
        assert zombie.kill_calls == 0

        assert source.calls == 1
        assert source.make_before_break_requests == 0

        recorded: tuple[RestartDecision, ...] = get_restart_decisions()
        assert recorded, "the restart must be recorded"
        assert not newest_before or recorded[-1] is not newest_before[0], (
            "the restart must be recorded"
        )
        decision = recorded[-1]
        assert decision.reason == RestartReason.PROCESS_EXITED
        assert decision.stage == "process_exited"
        assert decision.exit_status == _ZOMBIE_EXIT_STATUS
        assert decision.probe_duration_s is None
    finally:
        bridge.shutdown()

    assert _child_pids() == children_before, "test leaked a child process"
    assert threading.active_count() <= threads_before, "test leaked a thread"
    assert _open_fd_count() <= fds_before, "test leaked a file descriptor"


def test_e54_bridge_separates_zombie_from_live_child_on_the_same_alive_pid(
    tmp_path: Path,
) -> None:
    fds_before = _open_fd_count()
    threads_before = threading.active_count()
    children_before = _child_pids()

    live_pid = os.getpid()
    assert _bare_pid_probe_says_alive(live_pid)

    zombie_bridge, _zombie_source = _make_bridge(
        _make_inner(
            _ZombieChild(pid=live_pid, exit_status=_ZOMBIE_EXIT_STATUS),
            tmp_path / "zombie-session.json",
        ),
        _make_inner(
            _LiveChild(pid=_REPLACEMENT_PID),
            tmp_path / "zombie-replacement.json",
        ),
    )
    serving = _LiveChild(pid=live_pid)
    live_bridge, live_source = _make_bridge(
        _make_inner(serving, tmp_path / "live-session.json"),
        _make_inner(
            _LiveChild(pid=_SECOND_REPLACEMENT_PID),
            tmp_path / "live-replacement.json",
        ),
    )

    try:
        assert zombie_bridge.check_health_and_restart_if_needed() is True
        assert zombie_bridge.restart_count == 1

        assert live_bridge.check_health_and_restart_if_needed() is False
        assert live_bridge.restart_count == 0
        assert live_bridge.process.pid == live_pid
        assert live_source.calls == 0, "a healthy child must not be replaced"
        assert serving.terminate_calls == 0
        assert serving.kill_calls == 0
    finally:
        zombie_bridge.shutdown()
        live_bridge.shutdown()

    assert _child_pids() == children_before, "test leaked a child process"
    assert threading.active_count() <= threads_before, "test leaked a thread"
    assert _open_fd_count() <= fds_before, "test leaked a file descriptor"
