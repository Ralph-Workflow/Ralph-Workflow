"""AC-07: TTY and non-TTY rendering of ParallelDisplay produce the same logical lines.

Constructs two ``ParallelDisplay`` instances with the same lifecycle calls
but different terminal modes: one with ``force_terminal=True`` (TTY) and
one with ``force_terminal=False, color_system=None`` (non-TTY). Strips ANSI
escape codes from both and asserts the same logical lines appear, modulo
color/glyph differences. The test enforces the single-source-of-truth
invariant: only colors/glyphs should differ between TTY and non-TTY output,
not line order, count, or content.
"""

from __future__ import annotations

import io
import re

from rich.console import Console

from ralph.display._run_start_orientation import RunStartOrientation
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?[+\-]\d{2}:\d{2}")
_ELAPSED_RE = re.compile(r"elapsed=\d+\.\d+s")


def _normalize(text: str) -> str:
    """Strip presentation-independent runtime values before comparing lines."""
    without_ansi = _ANSI_ESCAPE_RE.sub("", text)
    without_timestamps = _TIMESTAMP_RE.sub("<TS>", without_ansi)
    return _ELAPSED_RE.sub("elapsed=<DURATION>", without_timestamps)


def _make_display(*, force_terminal: bool) -> tuple[ParallelDisplay, io.StringIO]:
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=force_terminal,
        color_system=("standard" if force_terminal else None),
        width=120,
    )
    ctx = make_display_context(console=console, env={"CI": "1"})
    return ParallelDisplay(ctx), buf


def _run_lifecycle(pd: ParallelDisplay) -> None:
    pd.emit_run_start(RunStartOrientation())
    pd.begin_phase("planning")
    pd.emit_phase_close("planning", "artifacts")
    pd.emit_run_end(phase="final")
    pd.emit(unit_id="unit-1", line="hello world")


def test_tty_and_non_tty_render_same_logical_lines() -> None:
    pd_tty, buf_tty = _make_display(force_terminal=True)
    pd_no_tty, buf_no_tty = _make_display(force_terminal=False)

    _run_lifecycle(pd_tty)
    _run_lifecycle(pd_no_tty)

    text_tty = _normalize(buf_tty.getvalue())
    text_no_tty = _normalize(buf_no_tty.getvalue())

    assert text_tty, "TTY output must not be empty"
    assert text_no_tty, "non-TTY output must not be empty"

    assert text_tty == text_no_tty, (
        "TTY and non-TTY output must match modulo ANSI escape codes.\n"
        f"--- TTY ---\n{text_tty!r}\n--- non-TTY ---\n{text_no_tty!r}\n"
    )
