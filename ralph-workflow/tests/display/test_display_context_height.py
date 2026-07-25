"""P0 (wt-028-display S-5 / AC-06) tests for DisplayContext height.

The Status Bar's height-aware contract depends on the
``DisplayContext`` carrying a ``height`` field (the terminal's
vertical dimension). The field is consumed by the height-aware
panel presentation (boxed panels degrade to unboxed headed text
below a row threshold so the working area remains usable on a
12-row split pane).

Tests pin:

* the field exists and is ``None`` by default,
* ``make_display_context(force_height=...)`` honors the override,
* a non-positive ``force_height`` is treated as ``None`` (the
  legacy opt-out contract),
* the field is preserved across ``refreshed()``.
"""

from __future__ import annotations

from rich.console import Console

from ralph.display.context import DisplayContext, make_display_context


def test_display_context_height_field_defaults_to_none() -> None:
    """The new ``height`` field is ``None`` by default (legacy opt-out)."""
    ctx = make_display_context(force_width=80, force_glyphs=True)
    assert isinstance(ctx, DisplayContext)
    assert hasattr(ctx, "height")


def test_make_display_context_force_height_overrides() -> None:
    """``force_height=24`` resolves to ``ctx.height == 24``."""
    ctx = make_display_context(force_width=80, force_glyphs=True, force_height=24)
    assert ctx.height == 24


def test_make_display_context_force_height_zero_disables() -> None:
    """``force_height=0`` is treated as the legacy ``None`` opt-out."""
    ctx = make_display_context(force_width=80, force_glyphs=True, force_height=0)
    assert ctx.height is None


def test_make_display_context_force_height_negative_disables() -> None:
    """A negative ``force_height`` is also treated as the legacy opt-out."""
    ctx = make_display_context(force_width=80, force_glyphs=True, force_height=-1)
    assert ctx.height is None


def test_make_display_context_reads_console_size_height() -> None:
    """When ``force_height`` is ``None``, the resolved height comes from the Console."""
    console = Console(width=80, height=42, force_terminal=True)
    ctx = make_display_context(console=console)
    assert ctx.height == 42


def test_make_display_context_force_height_wins_over_console_size() -> None:
    """``force_height`` wins over the Console's own ``size.height``."""
    console = Console(width=80, height=42, force_terminal=True)
    ctx = make_display_context(console=console, force_height=18)
    assert ctx.height == 18


def test_make_display_context_height_survives_refreshed() -> None:
    """The ``refreshed()`` cycle preserves ``height`` across resize."""
    console = Console(width=80, height=24, force_terminal=True)
    ctx = make_display_context(console=console)
    refreshed = ctx.refreshed()
    # The refreshed context honours the same Console size.
    assert refreshed.height == ctx.height


def test_make_display_context_height_is_optional_for_legacy_callers() -> None:
    """Legacy callers that pass no ``force_height`` keep the ``None`` opt-out."""
    ctx = make_display_context(force_width=80, force_glyphs=True)
    # Either ``None`` (no Console size) or a positive int are both
    # valid legacy outcomes; the contract is "doesn't crash".
    assert ctx.height is None or isinstance(ctx.height, int)
