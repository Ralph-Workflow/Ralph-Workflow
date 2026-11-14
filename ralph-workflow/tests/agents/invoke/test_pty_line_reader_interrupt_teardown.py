from __future__ import annotations

import threading
from unittest.mock import MagicMock

from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer
from ralph.process.teardown import teardown_subtree


class _TrackTeardown:
    def __init__(self, teardown_calls: list[int]) -> None:
        self._teardown_calls = teardown_calls

    def teardown_subtree(
        self,
        host_pid: int,
        *,
        issuer: ProcessTerminationIssuer,
        owner_label: str = "invoke",
    ) -> None:
        del issuer, owner_label
        self._teardown_calls.append(host_pid)


class _BoomTeardown:
    def teardown_subtree(
        self,
        host_pid: int,
        *,
        issuer: ProcessTerminationIssuer,
        owner_label: str = "invoke",
    ) -> None:
        del host_pid, issuer, owner_label
        raise OSError("teardown boom")


def _make_reader_for_interrupt(teardown_calls: list[int] | None = None) -> PtyLineReader:
    reader = object.__new__(PtyLineReader)
    reader._handle = MagicMock()
    reader._handle.pid = 4242
    reader._handle.terminate = MagicMock()
    reader._handle.close = MagicMock()
    reader._monitor_stop = threading.Event()
    reader._process_teardown = None if teardown_calls is None else _TrackTeardown(teardown_calls)
    return reader


def test_on_interrupt_calls_close_terminate_and_teardown_subtree() -> None:
    teardown_calls: list[int] = []
    reader = _make_reader_for_interrupt(teardown_calls)
    handle = reader._handle

    reader._on_interrupt()

    assert handle.close.called, "_on_interrupt must still close the handle"
    assert handle.terminate.called, "_on_interrupt must call handle.terminate() to kill the child"
    terminate_kwargs = handle.terminate.call_args.kwargs
    assert terminate_kwargs.get("grace_period_s") == 0.5, (
        f"terminate must use grace_period_s=0.5 like the watchdog path; got {terminate_kwargs!r}"
    )
    assert teardown_calls == [4242], (
        f"teardown_subtree must be called with the handle pid; got {teardown_calls!r}"
    )
    assert reader._monitor_stop.is_set(), "_on_interrupt must signal _monitor_stop"


def test_on_interrupt_swallows_close_error() -> None:
    teardown_calls: list[int] = []
    reader = _make_reader_for_interrupt(teardown_calls)
    handle = reader._handle
    handle.close.side_effect = OSError("close boom")

    reader._on_interrupt()

    assert handle.terminate.called, "terminate must run even if close raised"
    assert teardown_calls == [4242], (
        f"teardown_subtree must run even if close raised; got {teardown_calls!r}"
    )


def test_on_interrupt_swallows_terminate_error() -> None:
    teardown_calls: list[int] = []
    reader = _make_reader_for_interrupt(teardown_calls)
    handle = reader._handle
    handle.terminate.side_effect = OSError("terminate boom")

    reader._on_interrupt()

    assert handle.close.called
    assert teardown_calls == [4242], (
        f"teardown_subtree must run even if terminate raised; got {teardown_calls!r}"
    )


def test_on_interrupt_swallows_teardown_subtree_error() -> None:
    reader = _make_reader_for_interrupt()
    reader._process_teardown = _BoomTeardown()

    reader._on_interrupt()


def test_on_interrupt_skips_teardown_when_pid_missing() -> None:
    teardown_calls: list[int] = []
    reader = _make_reader_for_interrupt(teardown_calls)
    reader._handle.pid = None

    reader._on_interrupt()

    assert teardown_calls == [], (
        f"teardown_subtree must be skipped when pid is None; got {teardown_calls!r}"
    )


def test_on_interrupt_real_teardown_subtree_call() -> None:
    teardown_subtree(2**30, issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN)
