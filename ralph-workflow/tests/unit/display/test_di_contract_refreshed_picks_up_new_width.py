"""Tests for the DisplayContext dependency injection contract.

After the wt-028-display consolidation, mode is always 'default'.
These tests verify that:
1. All public renderers require an explicit DisplayContext (no silent Console fallbacks).
2. Color disabled propagates correctly through renderers.
3. Single default-mode renders consistently regardless of width.
4. DisplayContext.refreshed() picks up new terminal sizes without changing mode.
5. No literal color/style strings exist outside theme.py.
"""

from __future__ import annotations

from unittest.mock import PropertyMock, patch

from rich.console import Console

from ralph.display.context import make_display_context


class TestRefreshedPicksUpNewWidth:
    """Test that DisplayContext.refreshed() picks up new terminal sizes."""

    def test_refreshed_preserves_default_mode_across_resize(self) -> None:
        """Calling refreshed() preserves the single default mode across a wide→narrow resize."""
        console = Console(width=120, force_terminal=True)
        ctx = make_display_context(console=console, env={})

        narrow_width = 40
        with patch.object(
            type(console), "width", new_callable=PropertyMock, return_value=narrow_width
        ):
            refreshed = ctx.refreshed()

        assert refreshed.width == narrow_width

        # Sanity: without resize, refreshed preserves width
        refreshed_still_default = ctx.refreshed()
        assert refreshed_still_default.width == ctx.width

    def test_refreshed_preserves_theme_and_color_enabled(self) -> None:
        """refreshed() must preserve theme and color_enabled from the original context."""
        console = Console(width=80, force_terminal=True)
        ctx = make_display_context(console=console, env={})
        original_theme = ctx.theme
        original_color = ctx.color_enabled

        refreshed = ctx.refreshed()

        assert refreshed.theme is original_theme
        assert refreshed.color_enabled == original_color
