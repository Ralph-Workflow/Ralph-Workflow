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

from unittest.mock import PropertyMock, patch

from rich.console import Console

from ralph.display.context import make_display_context


class TestRefreshedPicksUpNewWidth:
    """Test that DisplayContext.refreshed() picks up new terminal sizes."""

    def test_refreshed_changes_mode(self) -> None:
        """Calling refreshed() on a wide context with narrow console switches to compact."""
        # Start with a console at width 120 (wide)
        console = Console(width=120, force_terminal=True)
        ctx = make_display_context(console=console, env={})
        assert ctx.mode == "wide"

        # Simulate resize: after refresh the console reports width 40
        narrow_width = 40
        with patch.object(
            type(console), "width", new_callable=PropertyMock, return_value=narrow_width
        ):
            refreshed = ctx.refreshed()

        assert refreshed.mode == "compact"
        assert refreshed.width == narrow_width

        # Sanity: without resize, refreshed stays wide
        refreshed_still_wide = ctx.refreshed()
        assert refreshed_still_wide.mode == "wide"

    def test_refreshed_preserves_theme_and_color_enabled(self) -> None:
        """refreshed() must preserve theme and color_enabled from the original context."""
        console = Console(width=80, force_terminal=True)
        ctx = make_display_context(console=console, env={})
        original_theme = ctx.theme
        original_color = ctx.color_enabled

        refreshed = ctx.refreshed()

        assert refreshed.theme is original_theme
        assert refreshed.color_enabled == original_color
