from __future__ import annotations

from unittest.mock import PropertyMock, patch

from rich.console import Console

from ralph.display.context import make_display_context


class TestRefreshedPicksUpNewWidth:
    def test_refreshed_preserves_default_mode_across_resize(self) -> None:
        console = Console(width=120, force_terminal=True)
        ctx = make_display_context(console=console, env={})

        narrow_width = 40
        with patch.object(
            type(console), "width", new_callable=PropertyMock, return_value=narrow_width
        ):
            refreshed = ctx.refreshed()

        assert refreshed.width == narrow_width

        refreshed_still_default = ctx.refreshed()
        assert refreshed_still_default.width == ctx.width

    def test_refreshed_preserves_theme_and_color_enabled(self) -> None:
        console = Console(width=80, force_terminal=True)
        ctx = make_display_context(console=console, env={})
        original_theme = ctx.theme
        original_color = ctx.color_enabled

        refreshed = ctx.refreshed()

        assert refreshed.theme is original_theme
        assert refreshed.color_enabled == original_color
