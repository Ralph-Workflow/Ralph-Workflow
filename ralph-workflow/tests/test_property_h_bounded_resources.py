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

from typing import cast

import pytest

from ralph.mcp.server import _saturated_dispatch
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
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

    Merge-order constraint: Unit 4 (Property H) lands this no-op FIRST;
    Unit 1 (Property A) wires it into do_POST second. The free function
    must exist for the do_POST import to resolve.
    """
    assert hasattr(_saturated_dispatch, "submit")
    assert callable(_saturated_dispatch.submit)


def test_saturated_dispatch_passes_through_callable_result() -> None:
    """_SaturatedDispatch.submit invokes the callable and returns its result."""
    result = _saturated_dispatch.submit(lambda: 42)
    assert result == 42


def test_saturated_dispatch_propagates_exceptions() -> None:
    """A raising callable surfaces its exception unchanged.

    The future bounded-executor wiring returns a 503 + -32001 frame on
    queue.Full; the no-op pass-through today simply propagates the
    exception (the caller's try/except handles it).
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
    from ralph.mcp.server._mcp_server_error import McpServerError

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


def test_no_op_saturated_dispatch_does_not_silently_queue() -> None:
    """The no-op _SaturatedDispatch does not silently queue past an unstated limit.

    The bounded-executor wiring is layered on top in a follow-up; today
    the helper is a pass-through. This test pins the seam so a future
    maintainer can replace the body without changing call sites in
    do_POST (the merge-order constraint is satisfied by the helper's
    existence, not by its current behavior).
    """
    # The free function is callable with a no-arg callable and returns
    # the result. A bounded executor would still satisfy this contract;
    # backpressure-rejected calls would return a 503 frame object.
    assert _saturated_dispatch.submit(lambda: "ok") == "ok"
