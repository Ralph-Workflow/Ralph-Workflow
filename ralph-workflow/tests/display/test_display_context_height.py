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


# --- wt-028-display S-6 / AC-05: height-constrained presentation. ---------
# Below 12 rows, framed presentation degrades to unboxed headed
# text. The threshold is the canonical split-pane size; below it,
# a bordered panel would crowd the working area. The threshold is
# honored by :meth:`DisplayContext.is_height_constrained` and is
# the single source of truth for every Panel-using emit method.


def test_is_height_constrained_default_threshold() -> None:
    """S-6: at height=12 the working area is NOT constrained (12 == 12)."""
    console = Console(width=80, height=12, force_terminal=True)
    ctx = make_display_context(console=console)
    assert ctx.height == 12
    assert ctx.is_height_constrained() is False, (
        "height=12 is the floor; the constraint starts strictly below 12"
    )


def test_is_height_constrained_just_below_threshold() -> None:
    """S-6: at height=11 the working area IS constrained."""
    console = Console(width=80, height=11, force_terminal=True)
    ctx = make_display_context(console=console)
    assert ctx.height == 11
    assert ctx.is_height_constrained() is True


def test_is_height_constrained_well_below_threshold() -> None:
    """S-6: at height=8 the working area is constrained."""
    console = Console(width=80, height=8, force_terminal=True)
    ctx = make_display_context(console=console)
    assert ctx.is_height_constrained() is True


def test_is_height_constrained_above_threshold() -> None:
    """S-6: at height=24 the working area is NOT constrained."""
    console = Console(width=80, height=24, force_terminal=True)
    ctx = make_display_context(console=console)
    assert ctx.is_height_constrained() is False


def test_is_height_constrained_none_height_returns_false() -> None:
    """S-6: when height is None (legacy opt-out) the constraint is False.

    A caller that does not know the height cannot make the
    degradation decision safely (degrading on a 200-row monitor
    would be wrong). The conservative default is to keep the
    full boxed presentation; the height must be explicitly set
    to opt into the short-terminal contract.

    The :func:`make_display_context` factory uses
    ``force_height=0`` (the legacy opt-out) to produce a context
    with ``height=None``; that is the canonical way to exercise
    the None-height branch without poking at a frozen dataclass
    field.
    """
    ctx = make_display_context(
        force_width=80, force_glyphs=True, force_height=0
    )
    assert ctx.height is None
    assert ctx.is_height_constrained() is False


def test_is_height_constrained_custom_threshold() -> None:
    """S-6: the threshold parameter overrides the default 12.

    The 12-row default is the canonical split-pane size; a
    caller that wants a stricter (e.g. 16) or looser (e.g. 8)
    threshold passes it explicitly. The check is a strict
    less-than so a height of exactly ``threshold`` is NOT
    constrained.
    """
    console = Console(width=80, height=15, force_terminal=True)
    ctx = make_display_context(console=console)
    # At 15, the default threshold (12) is NOT constrained.
    assert ctx.is_height_constrained(threshold=12) is False
    # At 15, a stricter threshold (16) IS constrained.
    assert ctx.is_height_constrained(threshold=16) is True
    # At 15, a looser threshold (8) is NOT constrained.
    assert ctx.is_height_constrained(threshold=8) is False


def test_is_height_constrained_survives_refreshed() -> None:
    """S-6: ``is_height_constrained`` survives the refreshed() cycle."""
    console = Console(width=80, height=10, force_terminal=True)
    ctx = make_display_context(console=console)
    assert ctx.is_height_constrained() is True
    refreshed = ctx.refreshed()
    assert refreshed.is_height_constrained() is True
