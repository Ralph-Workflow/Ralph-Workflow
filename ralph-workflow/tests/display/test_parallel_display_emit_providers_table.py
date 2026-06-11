"""Black-box tests for ``ParallelDisplay.emit_providers_table`` (wt-007).

Pins the new providers-table emit method. The test is black-box: it
constructs a StringIO-backed rich Console, attaches a DisplayContext,
and asserts the visible output. No real I/O, no time.sleep, no
subprocess.

Each test must complete in < 0.1s. The whole file is expected to
finish in < 0.5s.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.theme import RALPH_THEME


def _display() -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=False,
        width=120,
        color_system=None,
        theme=RALPH_THEME,
    )
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx), buf


def test_emit_providers_table_empty_list() -> None:
    """Empty provider list still renders the section rule and table title."""
    pd, buf = _display()
    pd.emit_providers_table([])
    pd.stop()
    output = buf.getvalue()
    assert "[providers]" in output, (
        f"expected section rule in output, got: {output!r}"
    )
    assert "Available Providers" in output, (
        f"expected 'Available Providers' title in output, got: {output!r}"
    )


def test_emit_providers_table_with_two_providers() -> None:
    """Multiple providers all appear in output with the section rule."""
    pd, buf = _display()
    pd.emit_providers_table(["openai", "anthropic"])
    pd.stop()
    output = buf.getvalue()
    assert "[providers]" in output, f"missing section rule: {output!r}"
    assert "openai" in output, f"missing provider name 'openai': {output!r}"
    assert "anthropic" in output, f"missing provider name 'anthropic': {output!r}"
    assert "Available Providers" in output, f"missing table title: {output!r}"


def test_emit_providers_table_quiet_mode_emits_nothing() -> None:
    """Quiet mode produces no output."""
    pd, buf = _display()
    pd._is_quiet = True
    pd.emit_providers_table([])
    pd.stop()
    assert buf.getvalue() == "", (
        f"quiet mode must produce no output, got: {buf.getvalue()!r}"
    )
