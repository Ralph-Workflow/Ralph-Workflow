"""Tests for DisplayContext.refreshed() and install_sigwinch_refresher.

After the wt-028-display consolidation, ``refreshed()`` preserves the
single ``default`` mode regardless of width changes. The historical
``compact`` / ``medium`` / ``wide`` tier is gone.
"""

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

    def test_refreshed_preserves_width_through_resize(self) -> None:
        """refreshed() preserves the resolved width across a wide→narrow resize."""
        console = Console(width=self.WIDE_WIDTH, force_terminal=True)
        ctx = make_display_context(console=console, env={})

        with patch.object(
            type(console), "width", new_callable=PropertyMock, return_value=self.NARROW_WIDTH
        ):
            refreshed = ctx.refreshed()

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

    def test_refreshed_preserves_default_mode_limits(self) -> None:
        """refreshed() preserves the single fixed default-mode limits."""
        console = Console(width=40, force_terminal=True)
        ctx = make_display_context(console=console, env={})
        compact_limit = ctx.headline_max_chars

        with patch.object(type(console), "width", new_callable=PropertyMock, return_value=200):
            refreshed = ctx.refreshed()

        assert refreshed.headline_max_chars == compact_limit

    def test_refreshed_preserves_console_identity(self) -> None:
        """refreshed() must use the same console instance."""
        console = Console(width=80, force_terminal=True)
        ctx = make_display_context(console=console, env={})

        refreshed = ctx.refreshed()

        assert refreshed.console is ctx.console

    def test_refreshed_preserves_columns_env(self) -> None:
        """refreshed() must preserve the COLUMNS env override after refresh.

        When the caller does NOT pass an explicit ``console=``
        argument, ``injected_console`` is False and ``COLUMNS``
        wins; ``refreshed()`` must keep that contract across a
        resize by re-reading the env on every recompute.
        """
        forced_narrow_width = self.NARROW_WIDTH
        ctx = make_display_context(env={"COLUMNS": str(forced_narrow_width)})
        assert ctx.width == forced_narrow_width

        refreshed = ctx.refreshed()

        # ``refreshed()`` re-runs width resolution; the
        # ``DisplayContext`` does not currently store the
        # ``injected_console`` flag so refreshed() falls through to
        # the COLUMNS env path. Document the asymmetry here; a follow-
        # up may store the flag so refreshed() stays consistent.
        assert refreshed.width == forced_narrow_width

    def test_refreshed_preserves_force_width(self) -> None:
        """refreshed() must preserve force_width override after refresh."""
        forced_narrow_width = self.NARROW_WIDTH
        console = Console(width=120, force_terminal=True)
        ctx = make_display_context(console=console, env={}, force_width=forced_narrow_width)
        assert ctx.width == forced_narrow_width

        refreshed = ctx.refreshed()

        assert refreshed.width == forced_narrow_width
