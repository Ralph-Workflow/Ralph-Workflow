"""ANSI-vs-plain regression tests for ralph/display/status.py."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.status import StatusSummary, display_phase, display_status_summary
from ralph.display.theme import RALPH_THEME


def _themed(buf: StringIO) -> Console:
    return Console(
        file=buf,
        force_terminal=True,
        no_color=False,
        color_system="truecolor",
        theme=RALPH_THEME,
        width=200,
        highlight=False,
    )


def _plain(buf: StringIO) -> Console:
    return Console(
        file=buf,
        force_terminal=False,
        color_system=None,
        theme=RALPH_THEME,
        width=200,
    )


def test_display_phase_emits_ansi_on_tty() -> None:
    buf = StringIO()
    display_phase("Planning", iteration=1, total=3, console=_themed(buf))
    assert "\x1b[" in buf.getvalue()


def test_display_phase_no_ansi_on_plain() -> None:
    buf = StringIO()
    display_phase("Planning", iteration=1, total=3, console=_plain(buf))
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "Phase: Planning" in out
    assert "Iteration 1 of 3" in out


def test_display_status_summary_emits_ansi_on_tty() -> None:
    summary = StatusSummary(
        phase="review",
        iteration=1,
        total_iterations=3,
        reviewer_pass=0,
        total_reviewer_passes=2,
        metrics={"calls": 5},
    )
    buf = StringIO()
    display_status_summary(summary, console=_themed(buf))
    assert "\x1b[" in buf.getvalue()


def test_display_status_summary_no_ansi_on_plain() -> None:
    summary = StatusSummary(
        phase="review",
        iteration=1,
        total_iterations=3,
        reviewer_pass=0,
        total_reviewer_passes=2,
        metrics={"calls": 5},
    )
    buf = StringIO()
    display_status_summary(summary, console=_plain(buf))
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "review" in out
    assert "1/3" in out
