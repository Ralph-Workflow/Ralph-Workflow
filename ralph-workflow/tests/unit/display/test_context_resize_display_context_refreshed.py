"""Tests for DisplayContext.refreshed() and install_sigwinch_refresher."""

from __future__ import annotations

from unittest.mock import PropertyMock, patch

from rich.console import Console

from ralph.display.context import DisplayContext, make_display_context


class TestDisplayContextRefreshed:
    """Tests for DisplayContext.refreshed()."""

    def test_refreshed_returns_new_instance(self) -> None:
        """refreshed() must return a new DisplayContext, not modify the original."""
        console = Console(width=120, force_terminal=True)
        ctx = make_display_context(console=console, env={})
        refreshed = ctx.refreshed()

        assert refreshed is not ctx
        assert isinstance(refreshed, DisplayContext)

    NARROW_WIDTH = 40
    WIDE_WIDTH = 120

    def test_refreshed_compact_to_wide(self) -> None:
        """A compact context refreshed with narrower console switches to compact."""
        # Start with a console at width 120 (wide)
        console = Console(width=self.WIDE_WIDTH, force_terminal=True)
        ctx = make_display_context(console=console, env={})
        assert ctx.mode == "wide"

        # Simulate resize: create a new context with the patched width
        with patch.object(
            type(console), "width", new_callable=PropertyMock, return_value=self.NARROW_WIDTH
        ):
            refreshed = ctx.refreshed()

        assert refreshed.mode == "compact"
        assert refreshed.width == self.NARROW_WIDTH

    def test_refreshed_wide_to_compact(self) -> None:
        """A wide context refreshed with narrower console becomes compact."""
        console = Console(width=self.WIDE_WIDTH, force_terminal=True)
        ctx = make_display_context(console=console, env={})
        assert ctx.mode == "wide"

        with patch.object(
            type(console), "width", new_callable=PropertyMock, return_value=self.NARROW_WIDTH
        ):
            refreshed = ctx.refreshed()

        assert refreshed.mode == "compact"
        assert refreshed.width == self.NARROW_WIDTH

    def test_refreshed_preserves_color_enabled(self) -> None:
        """refreshed() must preserve color_enabled from the original context."""
        console = Console(width=80, force_terminal=True)
        ctx = make_display_context(console=console, env={})
        original_color = ctx.color_enabled

        refreshed = ctx.refreshed()

        assert refreshed.color_enabled == original_color

    def test_refreshed_preserves_theme(self) -> None:
        """refreshed() must preserve the same theme object."""
        console = Console(width=80, force_terminal=True)
        ctx = make_display_context(console=console, env={})

        refreshed = ctx.refreshed()

        assert refreshed.theme is ctx.theme

    def test_refreshed_updates_headline_max_chars(self) -> None:
        """refreshed() must recompute headline_max_chars for the new width."""
        console = Console(width=40, force_terminal=True)
        ctx = make_display_context(console=console, env={})
        compact_limit = ctx.headline_max_chars

        with patch.object(type(console), "width", new_callable=PropertyMock, return_value=200):
            refreshed = ctx.refreshed()

        assert refreshed.headline_max_chars > compact_limit

    def test_refreshed_preserves_console_identity(self) -> None:
        """refreshed() must use the same console instance."""
        console = Console(width=80, force_terminal=True)
        ctx = make_display_context(console=console, env={})

        refreshed = ctx.refreshed()

        assert refreshed.console is ctx.console

    def test_refreshed_preserves_force_narrow_env(self) -> None:
        """refreshed() must keep compact mode when RALPH_FORCE_NARROW=1 is set."""
        console = Console(width=120, force_terminal=True)
        ctx = make_display_context(console=console, env={"RALPH_FORCE_NARROW": "1"})
        assert ctx.mode == "compact"

        refreshed = ctx.refreshed()

        assert refreshed.mode == "compact"

    def test_refreshed_preserves_columns_env(self) -> None:
        """refreshed() must preserve the COLUMNS env override after refresh."""
        forced_narrow_width = self.NARROW_WIDTH
        console = Console(width=120, force_terminal=True)
        ctx = make_display_context(console=console, env={"COLUMNS": str(forced_narrow_width)})
        assert ctx.mode == "compact"
        assert ctx.width == forced_narrow_width

        refreshed = ctx.refreshed()

        assert refreshed.width == forced_narrow_width
        assert refreshed.mode == "compact"

    def test_refreshed_preserves_force_width(self) -> None:
        """refreshed() must preserve force_width override after refresh."""
        forced_narrow_width = self.NARROW_WIDTH
        console = Console(width=120, force_terminal=True)
        ctx = make_display_context(console=console, env={}, force_width=forced_narrow_width)
        assert ctx.mode == "compact"
        assert ctx.width == forced_narrow_width

        refreshed = ctx.refreshed()

        assert refreshed.width == forced_narrow_width
        assert refreshed.mode == "compact"
