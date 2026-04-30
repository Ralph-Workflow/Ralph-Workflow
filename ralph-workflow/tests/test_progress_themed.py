"""ANSI-vs-plain regression tests for ralph/display/progress.py TextColumn."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.status import display_progress
from ralph.display.theme import RALPH_THEME


def test_display_progress_task_uses_theme_key_in_description() -> None:
    console = Console(
        file=StringIO(),
        force_terminal=True,
        color_system="truecolor",
        theme=RALPH_THEME,
        width=200,
        highlight=False,
    )
    ctx = make_display_context(console=console, env={})
    p = display_progress(current=0, total=10, phase="Planning", display_context=ctx)
    assert len(p.tasks) == 1
    assert "[theme.cat.meta]Planning[/theme.cat.meta]" in p.tasks[0].description


def test_theme_cat_meta_emits_ansi_on_tty() -> None:
    buf = StringIO()
    c = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        theme=RALPH_THEME,
        width=200,
        highlight=False,
    )
    c.print("[theme.cat.meta]Planning[/theme.cat.meta]")
    assert "\x1b[" in buf.getvalue()


def test_theme_cat_meta_no_ansi_on_plain() -> None:
    buf = StringIO()
    c = Console(
        file=buf,
        force_terminal=False,
        color_system=None,
        width=200,
    )
    c.print("[theme.cat.meta]Planning[/theme.cat.meta]")
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "Planning" in out
