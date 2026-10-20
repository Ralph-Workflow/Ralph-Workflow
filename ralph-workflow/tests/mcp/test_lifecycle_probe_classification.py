"""Tests for C3 probe failure classification and confirm-before-replace."""

from __future__ import annotations

import pathlib
import tempfile
import urllib.error
from datetime import timedelta
from unittest.mock import patch

from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess


class _FakeProcess:
    def __init__(self, poll_result: int | None = None, pid: int = 9999) -> None:
        self._poll_result = poll_result
        self._pid = pid
        self.terminated = False
        self.killed = False

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        return self._poll_result

    def terminate(self, grace_period_s: float = 5.0) -> None:
        self.terminated = True
        self._poll_result = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True
        self._poll_result = -9


def _make_inner(
    poll_result: int | None = None,
    pid: int = 9999,
    endpoint: str = "http://127.0.0.1:9100/mcp",
    session_file: pathlib.Path | None = None,
) -> StandaloneMcpProcess:
    if session_file is None:
        session_file = pathlib.Path(tempfile.mkdtemp()) / "session.json"
        session_file.write_text("{}", encoding="utf-8")
    return StandaloneMcpProcess(
        endpoint=endpoint,
        process=_FakeProcess(poll_result=poll_result, pid=pid),
        session_file=session_file,
    )


def _probe_bridge(
    probe_fn: object,
    *,
    contended: bool,
    clock_fn: object = None,
) -> lifecycle.RestartAwareMcpBridge:
    """Bridge with a scripted probe and an explicit contention signal."""
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


def _generic_runtime_error_probe(endpoint: str, timeout: timedelta) -> None:
    """A non-timeout, non-deferrable failure: a generic RuntimeError."""
    raise RuntimeError("unhealthy")


def _http_501_probe(endpoint: str, timeout: timedelta) -> None:
    """HTTP 501 from the upstream MCP endpoint: an HTTP 5xx outcome."""
    raise urllib.error.HTTPError(
        url="http://127.0.0.1:9100/mcp",
        code=501,
        msg="Not Implemented",
        hdrs={},
        fp=None,
    )


def _saturated_probe(endpoint: str, timeout: timedelta) -> None:
    """A saturated-dispatch outcome: surfaces the ``SaturatedResponse`` string."""
    raise RuntimeError("_saturated_dispatch: server saturated: try again later (code=-32001)")


def test_probe_timeout_deferred_under_fresh_contention() -> None:
    """TimeoutError probe failure IS deferred when fresh contention is present."""
    bridge = _probe_bridge(_timing_out_probe, contended=True)
    with patch.object(bridge, "_perform_restart") as mock_restart:
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is False
        assert mock_restart.call_count == 0
        assert bridge._consecutive_probe_failures_deferred == 1


def test_probe_connection_reset_not_deferred_under_contention() -> None:
    """ConnectionResetError is a real failure and must NOT be deferred even with contention."""
    bridge = _probe_bridge(_reset_probe, contended=True)
    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        assert bridge._consecutive_probe_failures_deferred == 0
        assert mock_restart.call_count == 1


def test_probe_reset_plus_overrun_not_deferred() -> None:
    """Reset co-occurring with probe overrun is NOT deferred (reset trumps overrun)."""
    clock = [1000.0]

    def reset_with_delay(endpoint: str, timeout: timedelta) -> None:
        clock[0] += 15.0  # overrun past the 5 s base probe timeout
        raise ConnectionResetError("Connection reset after delay")

    bridge = _probe_bridge(reset_with_delay, contended=True, clock_fn=lambda: clock[0])
    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        assert bridge._consecutive_probe_failures_deferred == 0
        assert mock_restart.call_count == 1


def test_probe_malformed_response_not_deferred() -> None:
    """Malformed response error is NOT deferred even under fresh contention."""
    bridge = _probe_bridge(_malformed_probe, contended=True)
    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        assert bridge._consecutive_probe_failures_deferred == 0
        assert mock_restart.call_count == 1


def test_confirm_before_replace_defers_on_confirm_success() -> None:
    """When ordinary probe times out but confirm probe succeeds, failure is deferred."""
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
        assert bridge._consecutive_probe_failures_deferred == 1


def test_confirm_before_replace_replaces_on_confirm_failure() -> None:
    """When ordinary probe times out AND confirm probe fails, replacement proceeds."""
    bridge = _probe_bridge(_timing_out_probe, contended=False)
    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        assert mock_restart.call_count == 1


def test_confirm_skipped_when_deferral_ceiling_reached() -> None:
    """When deferral ceiling is reached, confirm probe is skipped and restart forced."""
    bridge = _probe_bridge(_timing_out_probe, contended=True)
    bridge._consecutive_probe_failures_deferred = lifecycle._DEFERRAL_COUNT_CEILING

    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        assert mock_restart.call_count == 1


def test_probe_classifier_runtime_error_is_not_deferred() -> None:
    """A generic RuntimeError (non-timeout-class) is NEVER deferred, even under contention."""
    assert lifecycle._is_timeout_class_probe_outcome(RuntimeError("unhealthy"), False) is False
    assert lifecycle._is_timeout_class_probe_outcome(RuntimeError("unhealthy"), True) is False


def test_probe_classifier_http_501_is_not_deferred() -> None:
    """An HTTP 501 outcome is NEVER deferred, even with overrun=True."""
    exc = urllib.error.HTTPError(
        url="http://127.0.0.1:9100/mcp",
        code=501,
        msg="Not Implemented",
        hdrs={},
        fp=None,
    )
    assert lifecycle._is_timeout_class_probe_outcome(exc, False) is False
    assert lifecycle._is_timeout_class_probe_outcome(exc, True) is False


def test_probe_classifier_connection_reset_plus_overrun_is_not_deferred() -> None:
    """Connection reset co-occurring with overrun=True is NOT deferred (reset trumps overrun)."""
    exc = ConnectionResetError("Connection reset by peer")
    assert lifecycle._is_timeout_class_probe_outcome(exc, True) is False
    assert lifecycle._is_timeout_class_probe_outcome(exc, False) is False


def test_probe_classifier_timeout_class_is_deferred() -> None:
    """TimeoutError (builtin + socket.timeout alias) IS deferred."""
    assert lifecycle._is_timeout_class_probe_outcome(TimeoutError("probe timed out"), False) is True
    assert lifecycle._is_timeout_class_probe_outcome(TimeoutError("probe timed out"), True) is True
    assert lifecycle._is_timeout_class_probe_outcome(TimeoutError("socket timeout"), False) is True


def test_probe_classifier_overrun_only_outcome_is_deferred() -> None:
    """An overrun-only outcome (exc=None and overrun=True) IS deferred."""
    assert lifecycle._is_timeout_class_probe_outcome(None, True) is True
    assert lifecycle._is_timeout_class_probe_outcome(None, False) is False


def test_probe_runtime_error_does_not_defer_under_contention() -> None:
    """End-to-end: a generic RuntimeError probe never increments the deferral counter."""
    bridge = _probe_bridge(_generic_runtime_error_probe, contended=True)
    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        assert bridge._consecutive_probe_failures_deferred == 0
        assert mock_restart.call_count == 1


def test_probe_http_501_does_not_defer_under_contention() -> None:
    """End-to-end: HTTP 501 probe never defers even under fresh contention."""
    bridge = _probe_bridge(_http_501_probe, contended=True)
    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        assert bridge._consecutive_probe_failures_deferred == 0
        assert mock_restart.call_count == 1


def test_probe_connection_reset_plus_overrun_does_not_defer() -> None:
    """End-to-end: ConnectionResetError co-occurring with overrun=False under contention."""
    clock = [1000.0]

    def reset_with_overrun(endpoint: str, timeout: timedelta) -> None:
        clock[0] += 15.0
        raise ConnectionResetError("Connection reset after delay")

    bridge = _probe_bridge(reset_with_overrun, contended=True, clock_fn=lambda: clock[0])
    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        assert bridge._consecutive_probe_failures_deferred == 0
        assert mock_restart.call_count == 1


def test_probe_timeout_class_deferral_counter_increments_under_contention() -> None:
    """End-to-end: TimeoutError probe IS deferred; the deferral counter increments."""
    bridge = _probe_bridge(_timing_out_probe, contended=True)
    with patch.object(bridge, "_perform_restart") as mock_restart:
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is False
        assert mock_restart.call_count == 0
        assert bridge._consecutive_probe_failures_deferred == 1


def test_probe_saturated_dispatch_response_is_busy_not_a_failure() -> None:
    """A ``_saturated_dispatch`` outcome classifies as busy, not a failure."""
    bridge = _probe_bridge(_saturated_probe, contended=True)
    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        assert bridge._consecutive_probe_failures == 0
        assert bridge._consecutive_probe_failures_deferred == 0
        assert mock_restart.call_count == 0


def test_probe_saturated_dispatch_does_not_classify_as_timeout_class() -> None:
    """A ``_saturated_dispatch`` outcome is not timeout-class; the busy path owns it."""
    exc = RuntimeError("_saturated_dispatch: server saturated: try again later")
    assert lifecycle._is_timeout_class_probe_outcome(exc, True) is False
    assert lifecycle._is_timeout_class_probe_outcome(exc, False) is False
    assert lifecycle._is_saturated_dispatch_outcome(exc) is True
