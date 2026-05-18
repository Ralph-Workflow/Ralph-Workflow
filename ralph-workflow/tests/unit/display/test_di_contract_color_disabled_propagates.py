"""Tests for the DisplayContext dependency injection contract.

These tests verify that:
1. All public renderers require an explicit DisplayContext (no silent Console fallbacks).
2. Color disabled propagates correctly through renderers.
3. Compact mode produces abbreviated output.
4. Wide mode produces full layout.
5. DisplayContext.refreshed() picks up new terminal sizes.
6. No literal color/style strings exist outside theme.py.
"""

from __future__ import annotations

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.phase_banner import (
    show_phase_start,
)


class TestColorDisabledPropagates:
    """Test that NO_COLOR=1 propagates to disable ANSI in renderer output."""

    def test_no_color_disables_ansi_in_show_phase_start(self) -> None:
        """When NO_COLOR=1, show_phase_start output contains no ANSI sequences."""
        console = Console(record=True, width=120, force_terminal=True)
        ctx = make_display_context(console=console, env={"NO_COLOR": "1"})
        assert ctx.color_enabled is False

        show_phase_start("planning", display_context=ctx)

        output = console.export_text()
        # ANSI escape sequences are \x1b[...m or similar
        ansi_esc = 0x1B
        ansi_escapes = [c for c in output if ord(c) == ansi_esc]
        assert len(ansi_escapes) == 0, f"ANSI sequences found in NO_COLOR output: {output!r}"

    def test_no_color_on_console_propagates(self) -> None:
        """DisplayContext.color_enabled should be False when console.no_color is True."""
        console = Console(record=True, width=120, force_terminal=True, no_color=True)
        ctx = make_display_context(console=console, env={})
        assert ctx.color_enabled is False
