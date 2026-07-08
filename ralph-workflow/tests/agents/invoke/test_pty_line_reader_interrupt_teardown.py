"""Black-box tests for ``PtyLineReader._on_interrupt`` teardown path.

wt-024 memory-perf AC-02: ``_on_interrupt`` previously only called
``self._handle.close()`` and left the descendant process tree alive.
The watchdog-fire path at ``_check_fire`` already does the right thing
(terminate + teardown_subtree). The interrupt path must mirror it so a
Ctrl-C / BaseException does not leak child agents.

This test exercises ``_on_interrupt`` via a hand-crafted
``PtyLineReader`` instance with stub ``_handle`` / ``_monitor_stop``
so it runs without a real PTY / subprocess / wall-clock sleep.

It asserts:
1. ``_on_interrupt`` calls ``self._handle.close()`` (existing behavior)
2. ``_on_interrupt`` calls ``self._handle.terminate(grace_period_s=0.5)``
   (new behavior - mirrors the watchdog-fire path)
3. ``_on_interrupt`` calls ``teardown_subtree(self._handle.pid)``
   (new behavior)
4. Both ``close`` and ``terminate`` errors are swallowed (the
   existing ``contextlib.suppress(Exception)`` covers them; the new
   ``teardown_subtree`` call is also wrapped in
   ``contextlib.suppress(Exception)``).

All tests are unit tests - no real PTY, no real subprocess, no
wall-clock sleeps.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import ralph.agents.invoke._pty_line_reader as _pty_module
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.process.teardown import teardown_subtree

if TYPE_CHECKING:
    from collections.abc import Callable

if TYPE_CHECKING:
    import pytest


def _make_reader_for_interrupt() -> PtyLineReader:
    """Build a PtyLineReader with stub ``_handle`` / ``_monitor_stop``."""
    reader = object.__new__(PtyLineReader)
    reader._handle = MagicMock()
    reader._handle.pid = 4242
    reader._handle.terminate = MagicMock()
    reader._handle.close = MagicMock()
    reader._monitor_stop = threading.Event()
    return reader


def _track_teardown(teardown_calls: list[int]) -> Callable[[int], None]:
    def _record(pid: int) -> None:
        teardown_calls.append(pid)

    return _record


def test_on_interrupt_calls_close_terminate_and_teardown_subtree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_on_interrupt must call close() AND terminate() AND teardown_subtree(pid)."""
    teardown_calls: list[int] = []
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_line_reader.teardown_subtree",
        _track_teardown(teardown_calls),
    )
    reader = _make_reader_for_interrupt()
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


def test_on_interrupt_swallows_close_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing close() must NOT prevent terminate() / teardown_subtree from running."""
    teardown_calls: list[int] = []
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_line_reader.teardown_subtree",
        _track_teardown(teardown_calls),
    )

    reader = _make_reader_for_interrupt()
    handle = reader._handle
    handle.close.side_effect = OSError("close boom")

    reader._on_interrupt()

    assert handle.terminate.called, "terminate must run even if close raised"
    assert teardown_calls == [4242], (
        f"teardown_subtree must run even if close raised; got {teardown_calls!r}"
    )


def test_on_interrupt_swallows_terminate_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing terminate() must NOT prevent teardown_subtree from running."""
    teardown_calls: list[int] = []
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_line_reader.teardown_subtree",
        _track_teardown(teardown_calls),
    )

    reader = _make_reader_for_interrupt()
    handle = reader._handle
    handle.terminate.side_effect = OSError("terminate boom")

    reader._on_interrupt()

    assert handle.close.called
    assert teardown_calls == [4242], (
        f"teardown_subtree must run even if terminate raised; got {teardown_calls!r}"
    )


def test_on_interrupt_swallows_teardown_subtree_error() -> None:
    """A failing teardown_subtree() must NOT raise out of _on_interrupt."""
    reader = _make_reader_for_interrupt()

    original = _pty_module.teardown_subtree
    _pty_module.teardown_subtree = MagicMock(side_effect=OSError("teardown boom"))
    try:
        reader._on_interrupt()
    finally:
        _pty_module.teardown_subtree = original


def test_on_interrupt_skips_teardown_when_pid_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the handle has no pid attribute (or it is None), teardown_subtree must be skipped."""
    teardown_calls: list[int] = []
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_line_reader.teardown_subtree",
        _track_teardown(teardown_calls),
    )

    reader = _make_reader_for_interrupt()
    reader._handle.pid = None

    reader._on_interrupt()

    assert teardown_calls == [], (
        f"teardown_subtree must be skipped when pid is None; got {teardown_calls!r}"
    )


def test_on_interrupt_real_teardown_subtree_call() -> None:
    """Sanity-check that the production ``teardown_subtree`` is callable and
    tolerates a nonexistent pid."""
    teardown_subtree(2**30)
