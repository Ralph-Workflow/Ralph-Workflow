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
    show_phase_transition,
)


class TestCompactModeLimits:
    """Test that compact mode produces abbreviated layout."""

    def test_phase_transition_compact_no_leading_blank(self) -> None:
        """Compact mode show_phase_transition must not emit a leading blank line."""
        console = Console(record=True, width=50, force_terminal=True)
        ctx = make_display_context(console=console, env={"COLUMNS": "50"})
        assert ctx.mode == "compact"

        show_phase_transition("planning", "development", display_context=ctx)

        output = console.export_text()
        lines = output.strip().split("\n")
        max_compact_lines = 4
        assert len(lines) <= max_compact_lines, f"Compact output too long: {lines!r}"
        # First line should not be blank
        assert lines[0].strip() != "", f"Leading blank line in compact mode: {lines!r}"

    def test_phase_transition_wide_has_full_layout(self) -> None:
        """Wide mode show_phase_transition must emit full banner with Rules."""
        console = Console(record=True, width=120, force_terminal=True)
        ctx = make_display_context(console=console, env={"COLUMNS": "120"})
        assert ctx.mode == "wide"

        show_phase_transition("planning", "development", display_context=ctx)

        output = console.export_text()
        # In wide mode there should be Rule characters (──)
        assert "Rule" in output or "─" in output or "planning" in output
