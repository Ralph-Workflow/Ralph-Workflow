"""Tests for C3 probe failure classification and confirm-before-replace.

Every assertion in this file is expressed through the bridge's public
surface: the return value of ``check_health_and_restart_if_needed()``, the
public ``restart_count`` property, and the immutable ``diagnostics()``
snapshot.  The probe-outcome classification rule itself is published as
``ralph.mcp.bridge_diagnostics.classify_probe_failure`` and is the exact
function the lifecycle delegates to, so the classifier cases below cannot
drift from production behaviour.

Every session file these cases create is owned by pytest's ``tmp_path``
fixture: the helpers take the fixture-owned directory as an argument, so
no unmanaged directory is created on the host and no case leaves state
behind after the test session ends.
"""

from __future__ import annotations

import pathlib
import urllib.error
from datetime import timedelta

from ralph.mcp.server import (
    McpRestartPolicy,
    StandaloneMcpProcess,
    classify_probe_failure,
    lifecycle,
)


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
    tmp_path: pathlib.Path,
    poll_result: int | None = None,
    pid: int = 9999,
    endpoint: str = "http://127.0.0.1:9100/mcp",
) -> StandaloneMcpProcess:
    """Build an inner process whose session file is owned by ``tmp_path``.

    The session file lives inside pytest's ``tmp_path`` fixture, so the
    directory is torn down with the test session instead of leaking an
    unmanaged directory onto the host.
    """
    session_file = tmp_path / f"session_{pid}.json"
    session_file.write_text("{}", encoding="utf-8")
    return StandaloneMcpProcess(
        endpoint=endpoint,
        process=_FakeProcess(poll_result=poll_result, pid=pid),
        session_file=session_file,
    )


def _probe_bridge(
    tmp_path: pathlib.Path,
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
        _make_inner(tmp_path, poll_result=None, pid=1001),
        restart_fn=lambda: _make_inner(tmp_path),
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


def test_probe_timeout_deferred_under_fresh_contention(tmp_path: pathlib.Path) -> None:
    """TimeoutError probe failure IS deferred when fresh contention is present."""
    bridge = _probe_bridge(tmp_path, _timing_out_probe, contended=True)
    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is False
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 1


def test_probe_connection_reset_not_deferred_under_contention(tmp_path: pathlib.Path) -> None:
    """ConnectionResetError is a real failure and must NOT be deferred even with contention."""
    bridge = _probe_bridge(tmp_path, _reset_probe, contended=True)
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge.diagnostics().deferred_probe_failures == 0
    assert bridge.restart_count == 1


def test_probe_reset_plus_overrun_not_deferred(tmp_path: pathlib.Path) -> None:
    """Reset co-occurring with probe overrun is NOT deferred (reset trumps overrun)."""
    clock = [1000.0]

    def reset_with_delay(endpoint: str, timeout: timedelta) -> None:
        clock[0] += 15.0  # overrun past the 5 s base probe timeout
        raise ConnectionResetError("Connection reset after delay")

    bridge = _probe_bridge(tmp_path, reset_with_delay, contended=True, clock_fn=lambda: clock[0])
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge.diagnostics().deferred_probe_failures == 0
    assert bridge.restart_count == 1


def test_probe_malformed_response_not_deferred(tmp_path: pathlib.Path) -> None:
    """Malformed response error is NOT deferred even under fresh contention."""
    bridge = _probe_bridge(tmp_path, _malformed_probe, contended=True)
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge.diagnostics().deferred_probe_failures == 0
    assert bridge.restart_count == 1


def test_confirm_before_replace_defers_on_confirm_success(tmp_path: pathlib.Path) -> None:
    """When ordinary probe times out but confirm probe succeeds, failure is deferred."""
    call_count = 0

    def probe_fn(endpoint: str, timeout: timedelta) -> None:
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            raise TimeoutError("ordinary probe timed out")

    bridge = _probe_bridge(tmp_path, probe_fn, contended=False)
    restarted = bridge.check_health_and_restart_if_needed()
    restarted = bridge.check_health_and_restart_if_needed()
    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is False
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 1


def test_confirm_before_replace_replaces_on_confirm_failure(tmp_path: pathlib.Path) -> None:
    """When ordinary probe times out AND confirm probe fails, replacement proceeds."""
    bridge = _probe_bridge(tmp_path, _timing_out_probe, contended=False)
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 1


def test_confirm_skipped_when_deferral_ceiling_reached(tmp_path: pathlib.Path) -> None:
    """When deferral ceiling is reached, confirm probe is skipped and restart forced.

    The ceiling is reached through the public health-check path: every
    contended timeout defers the replacement and advances the deferral
    counter, so the bridge reaches the ceiling on its own.
    """
    bridge = _probe_bridge(tmp_path, _timing_out_probe, contended=True)
    ceiling = bridge.diagnostics().probe_deferral_count_ceiling
    for _ in range(ceiling):
        assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.diagnostics().deferred_probe_failures == ceiling

    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True
    assert bridge.restart_count == 1


def test_probe_classifier_runtime_error_is_not_deferred() -> None:
    """A generic RuntimeError (non-timeout-class) is NEVER deferred, even under contention."""
    assert classify_probe_failure(RuntimeError("unhealthy")) == "fatal"
    assert classify_probe_failure(RuntimeError("unhealthy"), overrun=True) == "fatal"


def test_probe_classifier_http_501_is_not_deferred() -> None:
    """An HTTP 501 outcome is NEVER deferred, even with overrun=True."""
    exc = urllib.error.HTTPError(
        url="http://127.0.0.1:9100/mcp",
        code=501,
        msg="Not Implemented",
        hdrs={},
        fp=None,
    )
    assert classify_probe_failure(exc) == "fatal"
    assert classify_probe_failure(exc, overrun=True) == "fatal"


def test_probe_classifier_connection_reset_plus_overrun_is_not_deferred() -> None:
    """Connection reset co-occurring with overrun=True is NOT deferred (reset trumps overrun)."""
    exc = ConnectionResetError("Connection reset by peer")
    assert classify_probe_failure(exc, overrun=True) == "fatal"
    assert classify_probe_failure(exc, overrun=False) == "fatal"


def test_probe_classifier_timeout_class_is_deferred() -> None:
    """TimeoutError (builtin + socket.timeout alias) IS deferred."""
    assert classify_probe_failure(TimeoutError("probe timed out")) == "timeout"
    assert classify_probe_failure(TimeoutError("probe timed out"), overrun=True) == "timeout"
    assert classify_probe_failure(TimeoutError("socket timeout")) == "timeout"


def test_probe_classifier_overrun_only_outcome_is_deferred() -> None:
    """An overrun-only outcome (exc=None and overrun=True) IS deferred."""
    assert classify_probe_failure(None, overrun=True) == "timeout"
    assert classify_probe_failure(None, overrun=False) == "fatal"


def test_probe_runtime_error_does_not_defer_under_contention(tmp_path: pathlib.Path) -> None:
    """End-to-end: a generic RuntimeError probe never defers the replacement."""
    bridge = _probe_bridge(tmp_path, _generic_runtime_error_probe, contended=True)
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge.diagnostics().deferred_probe_failures == 0
    assert bridge.restart_count == 1


def test_probe_http_501_does_not_defer_under_contention(tmp_path: pathlib.Path) -> None:
    """End-to-end: HTTP 501 probe never defers even under fresh contention."""
    bridge = _probe_bridge(tmp_path, _http_501_probe, contended=True)
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge.diagnostics().deferred_probe_failures == 0
    assert bridge.restart_count == 1


def test_probe_connection_reset_plus_overrun_does_not_defer(tmp_path: pathlib.Path) -> None:
    """End-to-end: ConnectionResetError co-occurring with overrun=False under contention."""
    clock = [1000.0]

    def reset_with_overrun(endpoint: str, timeout: timedelta) -> None:
        clock[0] += 15.0
        raise ConnectionResetError("Connection reset after delay")

    bridge = _probe_bridge(tmp_path, reset_with_overrun, contended=True, clock_fn=lambda: clock[0])
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge.diagnostics().deferred_probe_failures == 0
    assert bridge.restart_count == 1


def test_probe_timeout_class_deferral_counter_increments_under_contention(
    tmp_path: pathlib.Path,
) -> None:
    """End-to-end: TimeoutError probe IS deferred; the deferral counter increments."""
    bridge = _probe_bridge(tmp_path, _timing_out_probe, contended=True)
    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is False
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 1


def test_probe_saturated_dispatch_response_is_busy_not_a_failure(tmp_path: pathlib.Path) -> None:
    """A ``_saturated_dispatch`` outcome classifies as busy, not a failure."""
    bridge = _probe_bridge(tmp_path, _saturated_probe, contended=True)
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    diagnostics = bridge.diagnostics()
    assert diagnostics.consecutive_probe_failures == 0
    assert diagnostics.deferred_probe_failures == 0
    assert bridge.restart_count == 0


def test_probe_saturated_dispatch_does_not_classify_as_timeout_class() -> None:
    """A ``_saturated_dispatch`` outcome is not timeout-class; the busy path owns it."""
    exc = RuntimeError("_saturated_dispatch: server saturated: try again later")
    assert classify_probe_failure(exc, overrun=True) == "saturated"
    assert classify_probe_failure(exc, overrun=False) == "saturated"
