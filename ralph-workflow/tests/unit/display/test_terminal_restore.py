"""Unit tests for terminal_restore module.

Drives terminal restoration through fakes and mock streams without real TTY I/O.
"""

from __future__ import annotations

import signal
import termios
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    import pytest

from ralph.cli.main import ensure_cli_terminal_restore, reset_cli_restore_state
from ralph.display.terminal_restore import (
    restore_terminal,
    restore_terminal_modes,
    snapshot_terminal_modes,
    terminal_restore_sequence,
)


class _FakeStream:
    def __init__(self, *, is_tty: bool, fd: int) -> None:
        self._is_tty = is_tty
        self._fd = fd
        self._contents = ""

    def isatty(self) -> bool:
        return self._is_tty

    def fileno(self) -> int:
        return self._fd

    def write(self, text: str) -> int:
        self._contents += text
        return len(text)

    def flush(self) -> None:
        return None

    def getvalue(self) -> str:
        return self._contents


def _make_dummy_tty_stream() -> _FakeStream:
    return _FakeStream(is_tty=True, fd=1)


def _make_dummy_non_tty_stream() -> _FakeStream:
    return _FakeStream(is_tty=False, fd=2)


def test_terminal_restore_sequence_contains_expected_mode_resets() -> None:
    seq = terminal_restore_sequence()
    assert "\x1b[?25h" in seq  # show cursor
    assert "\x1b[?1049l" in seq  # leave alt screen
    assert "\x1b[?1000l" in seq  # disable mouse
    assert "\x1b[?1002l" in seq
    assert "\x1b[?1003l" in seq
    assert "\x1b[?1006l" in seq
    assert "\x1b[?1015l" in seq
    assert "\x1b[?2004l" in seq  # disable bracketed paste
    assert "\x1b[?1004l" in seq  # disable focus reporting
    assert "\x1b[?1l" in seq  # normal cursor keys
    assert "\x1b>" in seq  # numeric keypad
    assert "\x1b[r" in seq  # reset scroll region
    assert "\x1b(B" in seq  # ASCII G0 character set
    assert "\x1b[?7h" in seq  # enable autowrap
    assert "\x1b[0m" in seq  # reset SGR
    for code in ("\x1b[?25h", "\x1b[?1049l", "\x1b[?1006l", "\x1b[?2004l", "\x1b[?1004l", "\x1b[?1l", "\x1b>", "\x1b[r", "\x1b(B"):
        assert seq.count(code) == 1
    assert seq.endswith("\r")


def test_restore_terminal_writes_sequence_on_tty_stream() -> None:
    stream = _make_dummy_tty_stream()
    restore_terminal(stream=stream, modes=None)
    content = stream.getvalue()
    assert "\x1b[?25h" in content
    assert "\x1b[?1049l" in content


def test_restore_terminal_dumb_tty_skips_escape_write_but_restores_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _make_dummy_tty_stream()
    modes: list[int | list[bytes | int]] = [1, 2, 3, 4, 5, 6, []]
    monkeypatch.setenv("TERM", "dumb")
    with patch("termios.tcsetattr") as set_attrs, patch("termios.tcflush") as flush, patch(
        "os.isatty", return_value=True
    ):
        restore_terminal(stream=stream, modes=modes)
    assert stream.getvalue() == ""
    flush.assert_called_once_with(1, termios.TCIFLUSH)
    set_attrs.assert_called_once_with(1, termios.TCSANOW, modes)


def test_restore_terminal_writes_nothing_on_non_tty_stream() -> None:
    stream = _make_dummy_non_tty_stream()
    restore_terminal(stream=stream, modes=None)
    assert stream.getvalue() == ""


def test_restore_terminal_flushes_pending_input_after_writing_sequence() -> None:
    stream = _make_dummy_tty_stream()
    with patch("termios.tcflush") as flush, patch("os.isatty", return_value=True):
        restore_terminal(stream=stream, modes=None)
    flush.assert_called_once_with(1, termios.TCIFLUSH)
    assert stream.getvalue() == terminal_restore_sequence()


def test_restore_terminal_uses_stderr_when_default_stdout_is_not_a_tty() -> None:
    stdout = _make_dummy_non_tty_stream()
    stderr = _make_dummy_tty_stream()
    with patch("ralph.display.terminal_restore.sys.stdout", stdout), patch(
        "ralph.display.terminal_restore.sys.stderr", stderr
    ), patch("termios.tcflush"), patch("os.isatty", return_value=True):
        restore_terminal(modes=None)
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == terminal_restore_sequence()


def test_restore_terminal_modes_swallows_raising_tcsetattr() -> None:
    fake_modes: list[int | list[bytes | int]] = [0, 0, 0, 0, 0, 0, []]
    with patch("termios.tcsetattr", side_effect=OSError("tcsetattr failed")), patch(
        "os.isatty", return_value=True
    ):
        result = restore_terminal_modes(fd=1, modes=fake_modes)
        assert result is False


def test_snapshot_and_restore_terminal_modes() -> None:
    fake_modes: list[int | list[bytes | int]] = [1, 2, 3, 4, 5, 6, [b"a"]]
    with patch("termios.tcgetattr", return_value=fake_modes) as mock_getattr, patch(
        "termios.tcsetattr"
    ) as mock_setattr, patch("os.isatty", return_value=True):
        snap = snapshot_terminal_modes(fd=1)
        assert snap == fake_modes
        mock_getattr.assert_called_once_with(1)

        ok = restore_terminal_modes(fd=1)
        assert ok is True
        mock_setattr.assert_called_once()


def test_restore_terminal_is_total_guarded_on_arbitrary_object() -> None:
    bogus_object: object = object()
    restore_terminal(stream=bogus_object, modes=None)


def test_cli_ensure_terminal_restore_installs_signal_handlers_once_and_chains_previous() -> None:
    reset_cli_restore_state()
    installed: dict[int, object] = {}
    previous_calls: list[int] = []
    writes: list[tuple[int, bytes]] = []

    def getter(signum: int) -> object:
        return lambda received, frame: previous_calls.append(received)

    def setter(signum: int, handler: object) -> None:
        installed[signum] = handler

    saved_modes: list[int | list[bytes | int]] = [1, 2, 3, 4, 5, 6, []]
    with patch("ralph.cli.main.threading.current_thread", return_value=__import__("threading").main_thread()), patch(
        "ralph.cli.main.os.write", side_effect=lambda fd, data: writes.append((fd, data)) or len(data)
    ), patch("ralph.cli.main._resolve_fd", return_value=1), patch(
        "ralph.cli.main.restore_terminal_modes"
    ) as restore_modes:
        ensure_cli_terminal_restore(signal_getter=getter, signal_setter=setter)
        from ralph.display.terminal_restore import set_global_snapshot

        set_global_snapshot(saved_modes)
        ensure_cli_terminal_restore(signal_getter=getter, signal_setter=setter)
        ensure_cli_terminal_restore(signal_getter=getter, signal_setter=setter)
        assert set(installed) == {signal.SIGTERM, signal.SIGHUP}
        handler = installed[signal.SIGTERM]
        assert callable(handler)
        handler(signal.SIGTERM, None)

    assert writes == [(1, terminal_restore_sequence().encode())]
    restore_modes.assert_called_once_with(fd=1)
    assert previous_calls == [signal.SIGTERM]
    reset_cli_restore_state()


def test_cli_ensure_terminal_restore_registers_once() -> None:
    reset_cli_restore_state()
    registered_hooks: list[object] = []

    def fake_register(fn: object) -> None:
        registered_hooks.append(fn)

    with patch("ralph.cli.main.snapshot_terminal_modes") as mock_snap:
        ensure_cli_terminal_restore(register_fn=fake_register)
        mock_snap.assert_called_once()
        assert len(registered_hooks) == 1
        assert registered_hooks[0] is restore_terminal

        # Second call is idempotent and registers nothing new
        ensure_cli_terminal_restore(register_fn=fake_register)
        assert len(registered_hooks) == 1
    reset_cli_restore_state()
