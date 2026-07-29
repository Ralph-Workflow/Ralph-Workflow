from __future__ import annotations

import threading

import ralph.mcp.server._saturated_dispatch as saturated_dispatch


def test_submit_returns_saturation_response_after_injected_deadline() -> None:
    release = threading.Event()

    def block() -> bool:
        return release.wait(timeout=1.0)

    dispatch = saturated_dispatch._SaturatedDispatch(max_workers=1, dispatch_timeout_seconds=0.05)
    try:
        result = dispatch.submit(block)
        assert isinstance(result, saturated_dispatch.SaturatedResponse)
    finally:
        release.set()
        dispatch.shutdown()


def test_submit_returns_fast_callable_value() -> None:
    dispatch = saturated_dispatch._SaturatedDispatch(max_workers=1, dispatch_timeout_seconds=0.05)
    try:
        assert dispatch.submit(lambda: "ok") == "ok"
    finally:
        dispatch.shutdown()


def test_timeout_cancels_queued_callable() -> None:
    release = threading.Event()
    executed: list[str] = []

    def block() -> bool:
        return release.wait(timeout=1.0)

    def record_queued() -> None:
        executed.append("queued")

    dispatch = saturated_dispatch._SaturatedDispatch(max_workers=1, dispatch_timeout_seconds=0.05)
    try:
        assert isinstance(dispatch.submit(block), saturated_dispatch.SaturatedResponse)
        assert isinstance(dispatch.submit(record_queued), saturated_dispatch.SaturatedResponse)
        release.set()
        assert dispatch.submit(lambda: "next") == "next"
        assert executed == []
    finally:
        release.set()
        dispatch.shutdown()
