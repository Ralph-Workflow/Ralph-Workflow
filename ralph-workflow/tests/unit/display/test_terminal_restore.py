"""Unit tests for terminal_restore module.

Drives terminal restoration through fakes and mock streams without real TTY I/O.
"""

from __future__ import annotations

from unittest.mock import patch

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
    assert "\x1b[?7h" in seq  # enable autowrap
    assert "\x1b[0m" in seq  # reset SGR
    assert seq.endswith("\r")


def test_restore_terminal_writes_sequence_on_tty_stream() -> None:
    stream = _make_dummy_tty_stream()
    restore_terminal(stream=stream, modes=None)
    content = stream.getvalue()
    assert "\x1b[?25h" in content
    assert "\x1b[?1049l" in content


def test_restore_terminal_writes_nothing_on_non_tty_stream() -> None:
    stream = _make_dummy_non_tty_stream()
    restore_terminal(stream=stream, modes=None)
    assert stream.getvalue() == ""


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
