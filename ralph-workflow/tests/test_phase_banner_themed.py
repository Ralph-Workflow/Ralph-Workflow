"""ANSI-vs-plain regression tests for ralph/display/phase_banner.py."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.phase_banner import show_phase_complete, show_phase_start, show_phase_transition
from ralph.display.theme import RALPH_THEME


def _themed_console(buf: StringIO) -> Console:
    return Console(
        file=buf,
        force_terminal=True,
        no_color=False,
        color_system="truecolor",
        theme=RALPH_THEME,
        width=200,
        highlight=False,
    )


def _plain_console(buf: StringIO) -> Console:
    return Console(
        file=buf,
        force_terminal=False,
        color_system=None,
        width=200,
    )


def test_show_phase_transition_emits_ansi_on_tty() -> None:
    buf = StringIO()
    show_phase_transition("planning", "development", console=_themed_console(buf))
    assert "\x1b[" in buf.getvalue()


def test_show_phase_transition_no_ansi_on_plain() -> None:
    buf = StringIO()
    show_phase_transition("planning", "development", console=_plain_console(buf))
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "Planning" in out
    assert "Development" in out


def test_show_phase_start_emits_ansi_on_tty() -> None:
    buf = StringIO()
    show_phase_start("development", console=_themed_console(buf))
    assert "\x1b[" in buf.getvalue()


def test_show_phase_start_no_ansi_on_plain() -> None:
    buf = StringIO()
    show_phase_start("development", console=_plain_console(buf))
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "Development" in out


def test_show_phase_complete_emits_ansi_on_tty() -> None:
    buf = StringIO()
    show_phase_complete("review", console=_themed_console(buf))
    assert "\x1b[" in buf.getvalue()


def test_show_phase_complete_no_ansi_on_plain() -> None:
    buf = StringIO()
    show_phase_complete("review", console=_plain_console(buf))
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "Review" in out
    assert "complete" in out


def test_minor_transition_emits_ansi_on_tty() -> None:
    buf = StringIO()
    show_phase_transition("development", "development_analysis", console=_themed_console(buf))
    assert "\x1b[" in buf.getvalue()


def test_minor_transition_no_ansi_on_plain() -> None:
    buf = StringIO()
    show_phase_transition("development", "development_analysis", console=_plain_console(buf))
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "Development" in out
    assert "Development Analysis" in out
