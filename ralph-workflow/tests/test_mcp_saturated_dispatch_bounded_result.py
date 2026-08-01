from __future__ import annotations

import threading

import ralph.mcp.server._saturated_dispatch as saturated_dispatch
from ralph.timeout_defaults import EXEC_MAX_TIMEOUT_MS, MCP_DISPATCH_TIMEOUT_SECONDS


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


def test_pool_saturation_rejects_at_admission_without_running_the_callable() -> None:
    release = threading.Event()
    executed: list[str] = []

    def block() -> bool:
        return release.wait(timeout=1.0)

    dispatch = saturated_dispatch._SaturatedDispatch(max_workers=1, dispatch_timeout_seconds=0.05)
    try:
        assert isinstance(dispatch.submit(block), saturated_dispatch.SaturatedResponse)
        result = dispatch.submit(lambda: executed.append("sentinel"))
        assert isinstance(result, saturated_dispatch.SaturatedResponse)
        assert executed == []
    finally:
        release.set()
        dispatch.shutdown()


def test_completed_call_releases_its_admission_slot() -> None:
    dispatch = saturated_dispatch._SaturatedDispatch(max_workers=1, dispatch_timeout_seconds=0.05)
    try:
        assert dispatch.submit(lambda: "first") == "first"
        assert dispatch.submit(lambda: "second") == "second"
    finally:
        dispatch.shutdown()


def test_default_dispatch_timeout_is_the_named_constant() -> None:
    assert (
        saturated_dispatch._SaturatedDispatch().dispatch_timeout_seconds
        == MCP_DISPATCH_TIMEOUT_SECONDS
    )
    assert MCP_DISPATCH_TIMEOUT_SECONDS > EXEC_MAX_TIMEOUT_MS / 1000.0


def test_saturated_call_is_never_queued_or_invoked() -> None:
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
        assert executed == []
    finally:
        release.set()
        dispatch.shutdown()
