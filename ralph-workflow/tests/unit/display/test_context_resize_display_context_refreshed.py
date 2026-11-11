from __future__ import annotations

from unittest.mock import PropertyMock, patch

from rich.console import Console

from ralph.display.context import DisplayContext, make_display_context


def _console_with_height(width: int, height: int) -> Console:
    return Console(width=width, height=height, force_terminal=True)


class TestDisplayContextRefreshed:
    def test_refreshed_returns_new_instance(self) -> None:
        console = Console(width=120, force_terminal=True)
        ctx = make_display_context(console=console, env={})
        refreshed = ctx.refreshed()

        assert refreshed is not ctx
        assert isinstance(refreshed, DisplayContext)

    NARROW_WIDTH = 40
    WIDE_WIDTH = 120

    def test_refreshed_recovers_after_below_floor_resize(self) -> None:
        console = Console(width=self.WIDE_WIDTH, force_terminal=True)
        ctx = make_display_context(console=console, env={})

        with patch.object(
            type(console), "width", new_callable=PropertyMock, side_effect=(20, 40, self.WIDE_WIDTH)
        ):
            below_floor = ctx.refreshed()
            at_floor = below_floor.refreshed()
            recovered = at_floor.refreshed()

        assert (below_floor.width, at_floor.width, recovered.width) == (20, 40, self.WIDE_WIDTH)

    def test_refreshed_preserves_color_enabled(self) -> None:
        console = Console(width=80, force_terminal=True)
        ctx = make_display_context(console=console, env={})
        original_color = ctx.color_enabled

        refreshed = ctx.refreshed()

        assert refreshed.color_enabled == original_color

    def test_refreshed_preserves_theme(self) -> None:
        console = Console(width=80, force_terminal=True)
        ctx = make_display_context(console=console, env={})

        refreshed = ctx.refreshed()

        assert refreshed.theme is ctx.theme

    def test_refreshed_preserves_default_mode_limits(self) -> None:
        console = Console(width=40, force_terminal=True)
        ctx = make_display_context(console=console, env={})
        compact_limit = ctx.headline_max_chars

        with patch.object(type(console), "width", new_callable=PropertyMock, return_value=200):
            refreshed = ctx.refreshed()

        assert refreshed.headline_max_chars == compact_limit

    def test_refreshed_preserves_console_identity(self) -> None:
        console = Console(width=80, force_terminal=True)
        ctx = make_display_context(console=console, env={})

        refreshed = ctx.refreshed()

        assert refreshed.console is ctx.console

    def test_refreshed_preserves_columns_env(self) -> None:
        forced_narrow_width = self.NARROW_WIDTH
        ctx = make_display_context(env={"COLUMNS": str(forced_narrow_width)})
        assert ctx.width == forced_narrow_width

        refreshed = ctx.refreshed()

        assert refreshed.width == forced_narrow_width

    def test_refreshed_preserves_force_width(self) -> None:
        forced_narrow_width = self.NARROW_WIDTH
        console = Console(width=120, force_terminal=True)
        ctx = make_display_context(console=console, env={}, force_width=forced_narrow_width)
        assert ctx.width == forced_narrow_width

        refreshed = ctx.refreshed()

        assert refreshed.width == forced_narrow_width

    def test_refreshed_recomputes_height_from_console(self) -> None:
        console = _console_with_height(80, 24)
        ctx = make_display_context(console=console, env={})
        assert ctx.height == 24

        resized = _console_with_height(80, 12)
        refreshed = make_display_context(console=resized, env={}).refreshed()

        assert refreshed.height == 12

    def test_refreshed_force_height_wins_over_console_resize(self) -> None:
        console = _console_with_height(80, 24)
        ctx = make_display_context(console=console, env={}, force_height=18)
        assert ctx.height == 18

        resized = _console_with_height(80, 42)
        refreshed = make_display_context(console=resized, env={}, force_height=18).refreshed()

        assert refreshed.height == 18

    def test_refreshed_zero_force_height_stays_none(self) -> None:
        console = _console_with_height(80, 24)
        ctx = make_display_context(console=console, env={}, force_height=0)
        assert ctx.height is None

        resized = _console_with_height(80, 12)
        refreshed = make_display_context(console=resized, env={}, force_height=0).refreshed()

        assert refreshed.height is None
