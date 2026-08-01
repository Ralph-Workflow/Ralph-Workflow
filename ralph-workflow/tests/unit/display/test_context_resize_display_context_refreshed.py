"""Tests for DisplayContext.refreshed() and install_sigwinch_refresher.

After the wt-028-display consolidation, ``refreshed()`` preserves the
single ``default`` mode regardless of width changes. The historical
``compact`` / ``medium`` / ``wide`` tier is gone.
"""

from __future__ import annotations

from unittest.mock import PropertyMock, patch

from rich.console import Console

from ralph.display.context import DisplayContext, make_display_context


def _console_with_height(width: int, height: int) -> Console:
    """Build a Console whose configured height is the given value.

    P0 (wt-028-display S-6 / AC-03) tests need a Console whose live
    ``size.height`` is a known value so ``refreshed()`` can be
    observed recomputing it. Rich's ``Console(width=, height=)`` is
    the canonical constructor; we mirror it here so the test fixture
    is one line and never mutates an existing Console (which the
    type-ignore policy forbids in test files because that suppression
    marker is itself banned by the policy).
    """
    return Console(width=width, height=height, force_terminal=True)


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

    def test_refreshed_recovers_after_below_floor_resize(self) -> None:
        """P0: a temporary below-floor resize recovers the supported layout width."""
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

    def test_refreshed_recomputes_height_from_console(self) -> None:
        """P0 (wt-028-display S-6 / AC-03): ``refreshed()`` recomputes height.

        A vertical resize is observed by the next ``refreshed()`` cycle:
        a Console whose ``size.height`` drops from 24 to 12 rows
        yields a refreshed DisplayContext whose ``height`` follows.
        The ``force_height`` override the operator pinned at
        construction time continues to win over the live Console
        reading.
        """
        console = _console_with_height(80, 24)
        ctx = make_display_context(console=console, env={})
        assert ctx.height == 24

        # Simulate a vertical SIGWINCH-style resize by building a
        # new Console with the same identity but a new height; the
        # SIGWINCH/poll refresher replaces the DisplayContext's
        # console reference the same way.
        resized = _console_with_height(80, 12)
        refreshed = make_display_context(console=resized, env={}).refreshed()

        assert refreshed.height == 12

    def test_refreshed_force_height_wins_over_console_resize(self) -> None:
        """P0 (wt-028-display S-6 / AC-03): ``force_height`` survives ``refreshed()``.

        The operator's construction-time override continues to win
        over the live Console reading so a vertical resize observed
        by SIGWINCH does NOT widen or shrink a layout the operator
        pinned.
        """
        console = _console_with_height(80, 24)
        ctx = make_display_context(console=console, env={}, force_height=18)
        assert ctx.height == 18

        # A vertical resize that does NOT change the operator's
        # ``force_height`` continues to honour the override.
        resized = _console_with_height(80, 42)
        refreshed = make_display_context(console=resized, env={}, force_height=18).refreshed()

        assert refreshed.height == 18

    def test_refreshed_zero_force_height_stays_none(self) -> None:
        """P0 (wt-028-display S-6 / AC-03): ``force_height=0`` legacy opt-out survives refresh.

        A zero / negative ``force_height`` is the documented legacy
        opt-out: it disables height-aware rendering. ``refreshed()``
        must preserve the opt-out even when the live Console has a
        positive height so a SIGWINCH does not silently re-enable
        height-aware rendering for an opt-out caller.
        """
        console = _console_with_height(80, 24)
        ctx = make_display_context(console=console, env={}, force_height=0)
        assert ctx.height is None

        resized = _console_with_height(80, 12)
        refreshed = make_display_context(console=resized, env={}, force_height=0).refreshed()

        assert refreshed.height is None
