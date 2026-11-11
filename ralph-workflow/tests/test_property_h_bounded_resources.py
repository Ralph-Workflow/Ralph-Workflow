from __future__ import annotations

import concurrent.futures

import pytest

import ralph.mcp.server._saturated_dispatch as _saturated_dispatch_mod
from ralph.mcp.server import _saturated_dispatch
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server.lifecycle import RestartAwareMcpBridge
from ralph.mcp.websearch import _bounded_sdk_call


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
    old_inner = _RecordingInner(exit_code=1)
    new_inner = _RecordingInner(exit_code=None)
    new_inner.endpoint = "http://127.0.0.1:8766/mcp"

    restart_calls: list[_RecordingInner] = []

    def restart_fn() -> _RecordingInner:
        restart_calls.append(new_inner)
        return new_inner

    bridge = RestartAwareMcpBridge(
        inner=old_inner,
        restart_fn=restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=1, cooldown_seconds=0.0),
        run_id="test-run",
    )

    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True
    assert bridge.restart_count == 1
    assert old_inner.shutdown_calls, "old inner must be shut down before respawn"
    assert bridge._inner is new_inner
    assert len(restart_calls) == 1


def test_no_restart_when_process_is_healthy() -> None:
    healthy_inner = _RecordingInner(exit_code=None)

    def restart_fn() -> _RecordingInner:
        return _RecordingInner(exit_code=None)

    bridge = RestartAwareMcpBridge(
        inner=healthy_inner,
        restart_fn=restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=1, cooldown_seconds=0.0),
        run_id="test-run",
    )
    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is False
    assert bridge.restart_count == 0


def test_saturated_dispatch_free_function_exists() -> None:
    assert hasattr(_saturated_dispatch, "submit")
    assert callable(_saturated_dispatch.submit)


def test_saturated_dispatch_returns_callable_result() -> None:
    result = _saturated_dispatch.submit(lambda: 42)
    assert result == 42


def test_saturated_dispatch_propagates_callable_exceptions() -> None:

    def raise_value_error() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        _saturated_dispatch.submit(raise_value_error)


def test_adversarial_restart_fn_raises_does_not_propagate() -> None:
    old_inner = _RecordingInner(exit_code=1)

    def broken_restart_fn() -> _RecordingInner:
        raise RuntimeError("respawn failed")

    bridge = RestartAwareMcpBridge(
        inner=old_inner,
        restart_fn=broken_restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=2, cooldown_seconds=0.0),
        run_id="test-run",
    )
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.restart_count == 1
    assert old_inner.shutdown_calls
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.restart_count == 2
    with pytest.raises(McpServerError) as excinfo:
        bridge.check_health_and_restart_if_needed()
    assert excinfo.value.restart_count == 2
    assert "restart budget exhausted" in str(excinfo.value)


def test_restart_count_caps_at_max_restarts() -> None:
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
        inner=inners[0],
        restart_fn=rotating_restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=1, cooldown_seconds=0.0),
        run_id="test-run",
    )
    bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 1
    inners[1].process._exit_code = 1
    with pytest.raises(McpServerError):
        bridge.check_health_and_restart_if_needed()


def test_saturated_dispatch_overflow_returns_saturation_response() -> None:
    isolated = _saturated_dispatch._SaturatedDispatch(max_workers=2)
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


def test_singleton_atexit_shutdown_calls_executor_shutdown() -> None:
    shutdown_calls: list[bool] = []

    class _RecordingExecutor:
        _max_workers: int = 1

        def shutdown(self, wait: bool = True) -> None:
            shutdown_calls.append(wait)

    _saturated_dispatch.submit(lambda: 1)
    recording = _RecordingExecutor()
    _saturated_dispatch._default_dispatch.install_executor(recording)

    assert callable(_saturated_dispatch._atexit_shutdown)
    _saturated_dispatch._atexit_shutdown()

    assert shutdown_calls == [False], (
        f"atexit hook must call executor.shutdown(wait=False); got {shutdown_calls!r}"
    )

    _saturated_dispatch.reset_default()


def test_bounded_sdk_call_singleton_atexit_shutdown() -> None:
    shutdown_calls: list[bool] = []

    class _RecordingExecutor:
        _max_workers: int = 1

        def shutdown(self, wait: bool = True) -> None:
            shutdown_calls.append(wait)

    _bounded_sdk_call.with_timeout(lambda: 1, 5.0)
    recording = _RecordingExecutor()
    _bounded_sdk_call._default_call.install_executor(recording)

    assert callable(_bounded_sdk_call._atexit_shutdown)
    _bounded_sdk_call._atexit_shutdown()

    assert shutdown_calls == [False], (
        f"atexit hook must call executor.shutdown(wait=False); got {shutdown_calls!r}"
    )

    _bounded_sdk_call.reset_default()


def test_singleton_atexit_uses_current_singleton_not_captured() -> None:
    shutdown_calls: list[bool] = []

    class _RecordingExecutor:
        _max_workers: int = 1

        def shutdown(self, wait: bool = True) -> None:
            shutdown_calls.append(wait)

    recording = _RecordingExecutor()
    fresh = _saturated_dispatch._SaturatedDispatch(max_workers=1)
    fresh.install_executor(recording)
    _saturated_dispatch_mod._default_dispatch = fresh

    try:
        _saturated_dispatch._atexit_shutdown()
        assert shutdown_calls == [False], (
            f"atexit hook must use current singleton; got {shutdown_calls!r}"
        )
    finally:
        _saturated_dispatch.reset_default()
        _saturated_dispatch_mod._default_dispatch = _saturated_dispatch._SaturatedDispatch()
