"""Black-box tests for DisplayContext factory and terminal-mode detection.

After the wt-028-display consolidation, ``DisplayContext.mode`` is
always the literal string ``"default"``. There is no width-based
dispatch. The adaptive limits come from a single fixed
``_DEFAULT_LIMITS`` set.
"""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from ralph.display import _mode_adaptive_limits as limits_module
from ralph.display import context as context_module
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.theme import RALPH_THEME

_NARROW_WIDTH = 50
_MEDIUM_WIDTH = 80
_WIDE_WIDTH = 120


def _recording_console(width: int = _WIDE_WIDTH) -> Console:
    """Create a wide recording console for deterministic test output."""
    return Console(file=StringIO(), force_terminal=False, width=width, theme=RALPH_THEME)


def test_default_context_has_themed_console_and_positive_width() -> None:
    """make_display_context() with no overrides yields a themed console and positive width."""
    ctx = make_display_context(env={})
    assert isinstance(ctx, DisplayContext)
    assert ctx.width > 0
    assert ctx.console is not None
    # Theme should be RALPH_THEME (same object or same styles)
    assert ctx.theme is RALPH_THEME


@pytest.mark.parametrize("columns", ["40", "50", "60", "80", "99", "100", "120", "200", "300"])
def test_any_columns_width_gives_default_mode(columns: str) -> None:
    """Any COLUMNS width produces the single default mode (no width-based dispatch).

    Locks the AC-02 invariant: ``DisplayContext.mode`` is always the
    literal string ``"default"`` regardless of COLUMNS width. The mode
    is a single Literal["default"] value — there is no width-based
    dispatch.
    """
    ctx = make_display_context(env={"COLUMNS": columns})
    assert ctx.width == int(columns)
    mode_attr = vars(ctx)["mode"]
    assert mode_attr == "default", (
        f"DisplayContext.mode must be 'default' at any COLUMNS width; "
        f"got {mode_attr!r} at width {ctx.width}"
    )


@pytest.mark.parametrize("columns", ["40", "50", "60", "80", "99", "100", "120", "200", "300"])
def test_any_columns_width_adaptive_limits_are_constant(columns: str) -> None:
    """All COLUMNS widths share the same fixed adaptive limits.

    After the wt-028-display consolidation, ``_DEFAULT_LIMITS`` is the
    single constant owned by ``ralph/display/_mode_adaptive_limits.py``
    and consumed by ``ralph/display/context.py``. The width-independent
    invariant means a single display mode has a single set of limits.
    """
    ctx = make_display_context(env={"COLUMNS": columns})
    assert ctx.headline_max_chars == 120
    assert ctx.condenser_soft_limit == 400
    assert ctx.condenser_hard_limit == 4000
    assert ctx.thinking_preview_min_chars == 80
    assert ctx.tool_result_headline_min_chars == 80


def test_default_limits_owner_is_mode_adaptive_limits_module() -> None:
    """_DEFAULT_LIMITS lives in ralph.display._mode_adaptive_limits (single owner)."""
    assert limits_module._DEFAULT_LIMITS is context_module._DEFAULT_LIMITS, (
        "_DEFAULT_LIMITS must be a single shared object identity owned by "
        "ralph.display._mode_adaptive_limits and consumed by ralph.display.context."
    )


def test_no_color_env_disables_color() -> None:
    """NO_COLOR=1 should produce color_enabled=False."""
    ctx = make_display_context(env={"NO_COLOR": "1", "COLUMNS": str(_WIDE_WIDTH)})
    assert ctx.color_enabled is False


def test_no_color_wins_over_force_color() -> None:
    """NO_COLOR wins over FORCE_COLOR per standard CLI conventions."""
    env = {"NO_COLOR": "1", "FORCE_COLOR": "1", "COLUMNS": str(_WIDE_WIDTH)}
    ctx = make_display_context(env=env)
    assert ctx.color_enabled is False


def test_injected_console_is_reused() -> None:
    """A provided console should be stored on the context unchanged."""
    console = _recording_console(width=200)
    ctx = make_display_context(env={"COLUMNS": "200"}, console=console)
    assert ctx.console is console


_FORCE_WIDTH = 150


def test_force_width_overrides_columns_env() -> None:
    """force_width takes precedence over COLUMNS env."""
    ctx = make_display_context(env={"COLUMNS": str(_NARROW_WIDTH)}, force_width=_FORCE_WIDTH)
    assert ctx.width == _FORCE_WIDTH


def test_default_mode_uses_single_fixed_limits() -> None:
    """Single default-mode uses one fixed set of adaptive limits regardless of width."""
    narrow = make_display_context(env={"COLUMNS": str(_NARROW_WIDTH)})
    medium = make_display_context(env={"COLUMNS": str(_MEDIUM_WIDTH)})
    wide = make_display_context(env={"COLUMNS": str(_WIDE_WIDTH)})
    assert narrow.headline_max_chars == medium.headline_max_chars == wide.headline_max_chars
    assert narrow.condenser_soft_limit == medium.condenser_soft_limit == wide.condenser_soft_limit
    assert narrow.condenser_hard_limit == medium.condenser_hard_limit == wide.condenser_hard_limit


def test_injected_no_color_console_disables_color() -> None:
    """Injected console with no_color=True reports color_enabled=False even without NO_COLOR env."""
    no_color_console = Console(
        file=StringIO(),
        force_terminal=False,
        no_color=True,
        width=_WIDE_WIDTH,
        theme=RALPH_THEME,
    )
    ctx = make_display_context(env={"COLUMNS": str(_WIDE_WIDTH)}, console=no_color_console)
    assert ctx.color_enabled is False


def test_injected_color_console_with_no_color_env_disables_color() -> None:
    """NO_COLOR env overrides even an explicitly color-enabled injected console."""
    color_console = Console(
        file=StringIO(),
        force_terminal=True,
        no_color=False,
        width=_WIDE_WIDTH,
        theme=RALPH_THEME,
    )
    ctx = make_display_context(
        env={"NO_COLOR": "1", "COLUMNS": str(_WIDE_WIDTH)},
        console=color_console,
    )
    assert ctx.color_enabled is False
