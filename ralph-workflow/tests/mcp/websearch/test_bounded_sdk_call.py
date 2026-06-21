"""Policy-compliant unit tests for ``ralph.mcp.websearch._bounded_sdk_call``.

The :func:`with_timeout` helper bounds third-party-SDK-backed websearch calls
(DDGS, Exa, Tavily) so a hung SDK cannot block the dispatch worker for the
full MCP client request timeout. These tests assert the contract without real
wall-clock waits, real subprocesses, or real network I/O.

The test suite uses ONLY policy-compliant patterns:

- ``threading.Event`` for blocking (interruptible, NOT ``time.sleep``).
- ``monkeypatch`` for stubbing the ``_executor`` test seam.
- ``install_default_executor`` / ``reset_default`` seams for the singleton
  executor.
- ``concurrent.futures.ThreadPoolExecutor(max_workers=...)`` for fake workers.

The test contract is "raises on timeout" or "returns the callable's value",
NOT "takes exactly N seconds". If the helper does not actually time out,
the test hangs and pytest fails it.
"""

from __future__ import annotations

import concurrent.futures
import threading
from importlib import import_module
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType


def _import_bounded() -> ModuleType:
    return import_module("ralph.mcp.websearch._bounded_sdk_call")


def test_fast_callable_returns_result() -> None:
    bounded = _import_bounded()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-fast")
    try:
        result = bounded.with_timeout(lambda: 42, timeout_seconds=1.0, _executor=executor)
    finally:
        executor.shutdown(wait=True)
    assert result == 42


def test_slow_callable_raises_websearch_error_with_label() -> None:
    bounded = _import_bounded()
    event = threading.Event()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-slow")
    raised: BaseException | None = None
    try:
        try:
            bounded.with_timeout(
                lambda: event.wait(timeout=10.0),
                timeout_seconds=0.05,
                label="ddgs",
                _executor=executor,
            )
        except bounded.WebSearchError as exc:
            raised = exc
    finally:
        event.set()
        executor.shutdown(wait=True)
    assert raised is not None
    message = str(raised)
    assert "ddgs" in message
    assert "0.05" in message


def test_raising_callable_propagates_original_exception() -> None:
    bounded = _import_bounded()

    class _MarkerError(RuntimeError):
        pass

    marker = _MarkerError("original-exception-marker")
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="test-raising"
    )
    try:
        with pytest.raises(_MarkerError) as exc_info:
            bounded.with_timeout(
                lambda: (_ for _ in ()).throw(marker),
                timeout_seconds=1.0,
                _executor=executor,
            )
    finally:
        executor.shutdown(wait=True)
    assert exc_info.value is marker


@pytest.mark.parametrize(
    "exc",
    [ValueError("v"), RuntimeError("r"), KeyError("k"), IndexError("i")],
)
def test_known_exception_types_propagate_unchanged(exc: BaseException) -> None:
    bounded = _import_bounded()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-known")
    try:
        with pytest.raises(type(exc)) as exc_info:
            bounded.with_timeout(
                lambda: (_ for _ in ()).throw(exc),
                timeout_seconds=1.0,
                _executor=executor,
            )
    finally:
        executor.shutdown(wait=True)
    assert exc_info.value is exc


def test_none_timeout_runs_callable_directly() -> None:
    bounded = _import_bounded()
    bounded.reset_default()
    bounded.shutdown()
    try:
        result = bounded.with_timeout(lambda: 7, timeout_seconds=None)
    finally:
        bounded.reset_default()
    assert result == 7


def test_install_default_executor_seam() -> None:
    bounded = _import_bounded()
    bounded.reset_default()
    try:
        fake_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="test-seam"
        )
        try:
            bounded.install_default_executor(fake_executor)
            result = bounded.with_timeout(lambda: 11, timeout_seconds=1.0)
            assert result == 11
        finally:
            fake_executor.shutdown(wait=True)
    finally:
        bounded.reset_default()


def test_concurrent_calls_all_obey_timeout() -> None:
    bounded = _import_bounded()
    events: list[threading.Event] = [threading.Event() for _ in range(5)]
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-conc")
    raised: list[BaseException] = []
    try:
        for event in events:

            def _blocker(ev: threading.Event = event) -> None:
                ev.wait(timeout=10.0)

            try:
                bounded.with_timeout(
                    _blocker, timeout_seconds=0.05, label="tavily", _executor=executor
                )
            except bounded.WebSearchError as exc:
                raised.append(exc)
    finally:
        for event in events:
            event.set()
        executor.shutdown(wait=True)
    assert len(raised) == 5
    for exc in raised:
        assert "tavily" in str(exc)
        assert "0.05" in str(exc)
