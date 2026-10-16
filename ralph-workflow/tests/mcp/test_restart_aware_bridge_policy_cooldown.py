"""S4 containment regression: McpRestartPolicy.cooldown_seconds spaces consecutive McpServerError restarts."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.subprocess_e2e


import contextlib
import pathlib
import tempfile

from ralph.mcp.server import lifecycle


class _FakeProcess:
    def __init__(self, poll_result=None, pid=9999):
        self._poll_result = poll_result
        self._pid = pid

    @property
    def pid(self):
        return self._pid

    def poll(self):
        return self._poll_result

    def terminate(self, grace_period_s=5.0):
        self._poll_result = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        return None


def _make_inner(poll_result):
    session_file = pathlib.Path(tempfile.mkdtemp()) / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    return lifecycle.StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9200/mcp",
        process=_FakeProcess(poll_result=poll_result),
        session_file=session_file,
    )


def test_cooldown_seconds_default_is_two() -> None:
    policy = lifecycle.McpRestartPolicy()
    assert policy.cooldown_seconds == 2.0
    for value in (0.0, 0.5, 5.0, 30.0):
        assert lifecycle.McpRestartPolicy(cooldown_seconds=value).cooldown_seconds == value


def test_cooldown_after_failure_honors_policy() -> None:
    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        return None

    inner = _make_inner(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None),
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3, cooldown_seconds=2.0),
        run_id="test-run",
        sleep_fn=fake_sleep,
    )
    bridge._cooldown_after_failure()
    assert sleep_calls == [2.0]


def test_cooldown_zero_short_circuits_no_sleep() -> None:
    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        return None

    inner = _make_inner(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None),
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
        sleep_fn=fake_sleep,
    )
    bridge._cooldown_after_failure()
    assert sleep_calls == []


def test_cooldown_after_failed_restart_sleeps_policy_value() -> None:
    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        return None

    inner = _make_inner(poll_result=1)

    def restart_fn():
        raise RuntimeError("synthetic")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3, cooldown_seconds=1.5),
        run_id="test-run",
        sleep_fn=fake_sleep,
    )
    with contextlib.suppress(lifecycle.McpServerError):
        bridge.check_health_and_restart_if_needed()
    assert sleep_calls == [1.5]
