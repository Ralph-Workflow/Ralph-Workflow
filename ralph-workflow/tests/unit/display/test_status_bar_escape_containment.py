"""Regression tests for terminal-escape containment at Status Bar sinks."""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from ralph.display import record_writer as record_writer_module
from ralph.display import status_bar as status_bar_module


def test_safe_single_line_removes_full_terminal_control_sequences() -> None:
    """Private-parameter CSI, OSC, and two-character ESC cannot leak into labels."""
    text = "before\x1b[<35;1;2Mmouse\x1b[>0cattrs\x1b]2;title\x07title\x1bMafter"

    assert status_bar_module._safe_single_line(text) == "beforemouseattrstitleafter"


def test_safe_single_line_keeps_tab_normalization_behavior() -> None:
    """Tabs remain one space so width budgeting has the established behavior."""
    assert status_bar_module._safe_single_line("plain\tlabel") == "plain label"


def test_record_scrubbers_remove_full_terminal_control_sequences() -> None:
    """The rendered record uses the canonical full-range terminal stripper."""
    text = "before\x1b[<35;1;2Mmouse\x1b[>0cattrs\x1b]2;title\x07title\x1bMafter"

    assert record_writer_module._strip_ansi(text) == "beforemouseattrstitleafter"
    assert record_writer_module._body_lines(text) == ("beforemouseattrstitleafter",)


class _TtyStringIO(StringIO):
    def isatty(self) -> bool:
        return True


def _status_bar_for_cleanup(stream: StringIO, *, is_tty: bool) -> status_bar_module.StatusBar:
    """Build the narrow lifecycle seam needed to exercise fallback cleanup."""
    status_bar = status_bar_module.StatusBar.__new__(status_bar_module.StatusBar)
    status_bar._fallback_rendered = True
    status_bar._fallback_frame = "prior"
    status_bar._display = SimpleNamespace(
        _ctx=SimpleNamespace(console=SimpleNamespace(file=stream, is_terminal=is_tty))
    )
    return status_bar


def test_fallback_cleanup_writes_no_escape_codes_to_non_tty() -> None:
    """Redirected fallback output must never include cursor erase controls."""
    stream = StringIO()
    status_bar = _status_bar_for_cleanup(stream, is_tty=False)

    status_bar._fallback_cleanup()

    assert stream.getvalue() == ""
    assert status_bar._fallback_rendered is False
    assert status_bar._fallback_frame is None


def test_fallback_cleanup_keeps_erase_for_vt_capable_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A VT-capable TTY retains the existing transient-footer erase behavior."""
    stream = _TtyStringIO()
    status_bar = _status_bar_for_cleanup(stream, is_tty=True)
    monkeypatch.setattr(status_bar_module, "terminal_understands_vt", lambda: True)

    status_bar._fallback_cleanup()

    assert stream.getvalue() == "\r\x1b[1A\x1b[2K"
