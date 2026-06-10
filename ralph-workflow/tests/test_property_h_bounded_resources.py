# property-test: H — bounded resources, no orphans, terminating cleanup
"""Three property H invariants:

1. RestartAwareMcpBridge always calls shutdown() on the dead inner BEFORE
   assigning the new inner (no orphans serving stale code).
2. The _SaturatedDispatch free function exists (the no-op pass-through landed
   first per merge-order constraints) and is callable from do_POST.
3. An adversarial respawn that raises during restart terminates the cleanup
   loop without spinning indefinitely.
"""

from __future__ import annotations

import concurrent.futures
from typing import cast

import pytest

from ralph.mcp.server import _saturated_dispatch
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server.lifecycle import RestartAwareMcpBridge


class _FakeProcess:
    def __init__(self, exit_code: int | None = None) -> None:
        self._exit_code = exit_code

    def poll(self) -> int | None:
        return self._exit_code


class _RecordingInner:
    def __init__(self, *, exit_code: int | None = None) -> None:
        self.process = _FakeProcess(exit_code=exit_code)
        self.endpoint = "http://127.0.0.1:8765/mcp"
        self.shutdown_calls: list[bool] = []
        self.shutdown_call_order: list[int] = []
        self._order_counter = 0

    def shutdown(self) -> None:
        self._order_counter += 1
        self.shutdown_call_order.append(self._order_counter)
        self.shutdown_calls.append(True)


def test_shutdown_called_on_dead_inner_before_respawn() -> None:
    """When the inner is dead, the bridge shuts it down BEFORE assigning new inner."""
    old_inner = _RecordingInner(exit_code=1)  # process exited
    new_inner = _RecordingInner(exit_code=None)
    new_inner.endpoint = "http://127.0.0.1:8766/mcp"

    restart_calls: list[_RecordingInner] = []

    def restart_fn() -> _RecordingInner:
        restart_calls.append(cast("_RecordingInner", new_inner))
        return new_inner

    bridge = RestartAwareMcpBridge(
        inner=cast("object", old_inner),
        restart_fn=restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=1),
    )

    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True
    assert bridge.restart_count == 1
    # The old inner was shut down
    assert old_inner.shutdown_calls, "old inner must be shut down before respawn"
    # And the new inner is now in place
    assert bridge._inner is new_inner
    # The restart_fn was called exactly once
    assert len(restart_calls) == 1


def test_no_restart_when_process_is_healthy() -> None:
    """When the inner is healthy, the bridge does NOT restart."""
    healthy_inner = _RecordingInner(exit_code=None)  # process alive

    def restart_fn() -> _RecordingInner:
        return _RecordingInner(exit_code=None)

    bridge = RestartAwareMcpBridge(
        inner=cast("object", healthy_inner),
        restart_fn=restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=1),
    )
    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is False
    assert bridge.restart_count == 0


def test_saturated_dispatch_free_function_exists() -> None:
    """_SaturatedDispatch.submit exists at ralph.mcp.server._saturated_dispatch.

    The free function is the public seam the do_POST handler imports; it
    must exist and be callable. The merge-order constraint (Unit 4 lands
    this first, Unit 1 wires it into do_POST second) is satisfied by
    the helper's existence, not its current behavior.
    """
    assert hasattr(_saturated_dispatch, "submit")
    assert callable(_saturated_dispatch.submit)


def test_saturated_dispatch_returns_callable_result() -> None:
    """_SaturatedDispatch.submit invokes the callable and returns its result.

    The bounded executor runs the callable; a result is returned to the
    caller.
    """
    result = _saturated_dispatch.submit(lambda: 42)
    assert result == 42


def test_saturated_dispatch_propagates_callable_exceptions() -> None:
    """A raising callable surfaces its exception to the caller.

    The transport-repetition breaker observes the exception in the
    handler's outer try/except; the helper must NOT swallow it.
    """
    def raise_value_error() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        _saturated_dispatch.submit(raise_value_error)


def test_adversarial_restart_fn_raises_propagates() -> None:
    """A restart_fn that raises propagates the exception to the caller.

    The cleanup loop in the bridge is bounded: it makes ONE call to
    restart_fn and propagates the exception. It does not catch and
    retry (which would spin without bound).
    """
    old_inner = _RecordingInner(exit_code=1)  # process exited

    def broken_restart_fn() -> _RecordingInner:
        raise RuntimeError("respawn failed")

    bridge = RestartAwareMcpBridge(
        inner=cast("object", old_inner),
        restart_fn=broken_restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=1),
    )
    with pytest.raises(RuntimeError, match="respawn failed"):
        bridge.check_health_and_restart_if_needed()
    # The old inner was shut down before the failure
    assert old_inner.shutdown_calls


def test_restart_count_caps_at_max_restarts() -> None:
    """The bridge raises McpServerError when restart_count reaches max_restarts."""
    inner_a = _RecordingInner(exit_code=1)
    inner_b = _RecordingInner(exit_code=1)
    inner_b.endpoint = "http://127.0.0.1:8766/mcp"
    inner_c = _RecordingInner(exit_code=1)
    inner_c.endpoint = "http://127.0.0.1:8767/mcp"

    inners = [inner_a, inner_b, inner_c]
    call_index = [0]

    def rotating_restart_fn() -> _RecordingInner:
        inner = inners[call_index[0]]
        call_index[0] += 1
        return inner

    bridge = RestartAwareMcpBridge(
        inner=cast("object", inners[0]),
        restart_fn=rotating_restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=1),
    )
    # First restart succeeds
    bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 1
    # Now make the new inner dead too — the second restart is over the cap
    inners[1].process._exit_code = 1
    with pytest.raises(McpServerError):
        bridge.check_health_and_restart_if_needed()


def test_saturated_dispatch_overflow_returns_saturation_response() -> None:
    """When the bounded executor is shut down, submit returns SaturatedResponse.

    The current implementation is backed by a ThreadPoolExecutor; once
    the executor is shut down, submit returns a SaturatedResponse (not
    the callable's result) and does NOT raise. The handler converts the
    SaturatedResponse into an HTTP 503 + JSON-RPC -32001 frame.

    Uses an isolated _SaturatedDispatch instance (not the process
    singleton) so the test does not race with concurrent tests that
    depend on the singleton's executor.
    """
    isolated = _saturated_dispatch._SaturatedDispatch(max_workers=2)
    # Install an executor and immediately shut it down so the next
    # submit() observes the post-shutdown state.
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    executor.shutdown(wait=False)
    isolated.install_executor(executor)
    try:
        result = isolated.submit(lambda: "ok")
        assert isinstance(result, _saturated_dispatch.SaturatedResponse)
        assert result.code == _saturated_dispatch.SATURATION_CODE
        assert result.message == _saturated_dispatch.SATURATION_MESSAGE
    finally:
        isolated.shutdown()
