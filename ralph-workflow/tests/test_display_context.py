"""Black-box tests for DisplayContext factory and terminal-mode detection."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import DisplayContext, make_display_context
from ralph.display.theme import RALPH_THEME

_NARROW_WIDTH = 50
_MEDIUM_WIDTH = 80
_WIDE_WIDTH = 120
_COMPACT_HEADLINE_MAX = 80
_MEDIUM_HEADLINE_MAX = 100
_WIDE_CONDENSER_SOFT = 400


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


def test_narrow_columns_env_gives_compact_mode() -> None:
    """COLUMNS=50 should produce compact mode with narrow=True."""
    ctx = make_display_context(env={"COLUMNS": str(_NARROW_WIDTH)})
    assert ctx.mode == "compact"
    assert ctx.narrow is True
    assert ctx.width == _NARROW_WIDTH


def test_wide_columns_env_gives_wide_mode() -> None:
    """COLUMNS=120 should produce wide mode."""
    ctx = make_display_context(env={"COLUMNS": str(_WIDE_WIDTH)})
    assert ctx.mode == "wide"
    assert ctx.narrow is False
    assert ctx.width == _WIDE_WIDTH


def test_medium_columns_env_gives_medium_mode() -> None:
    """COLUMNS=80 should produce medium mode with narrow=False."""
    ctx = make_display_context(env={"COLUMNS": str(_MEDIUM_WIDTH)})
    assert ctx.mode == "medium"
    assert ctx.narrow is False
    assert ctx.width == _MEDIUM_WIDTH


def test_no_color_env_disables_color() -> None:
    """NO_COLOR=1 should produce color_enabled=False."""
    ctx = make_display_context(env={"NO_COLOR": "1", "COLUMNS": str(_WIDE_WIDTH)})
    assert ctx.color_enabled is False


def test_no_color_wins_over_force_color() -> None:
    """NO_COLOR wins over FORCE_COLOR per standard CLI conventions."""
    env = {"NO_COLOR": "1", "FORCE_COLOR": "1", "COLUMNS": str(_WIDE_WIDTH)}
    ctx = make_display_context(env=env)
    assert ctx.color_enabled is False


def test_ralph_force_narrow_gives_compact_regardless_of_width() -> None:
    """RALPH_FORCE_NARROW=1 forces compact mode regardless of terminal width."""
    ctx = make_display_context(env={"RALPH_FORCE_NARROW": "1", "COLUMNS": "200"})
    assert ctx.mode == "compact"
    assert ctx.narrow is True


def test_headline_max_chars_adapts_to_mode() -> None:
    """compact mode headline_max_chars <= 80; wide mode headline_max_chars == 120."""
    compact = make_display_context(env={"COLUMNS": str(_NARROW_WIDTH)})
    wide = make_display_context(env={"COLUMNS": str(_WIDE_WIDTH)})
    assert compact.headline_max_chars <= _COMPACT_HEADLINE_MAX
    assert wide.headline_max_chars == _WIDE_WIDTH


def test_condenser_soft_limit_adapts_to_mode() -> None:
    """compact mode condenser_soft_limit < 400; wide mode condenser_soft_limit == 400."""
    compact = make_display_context(env={"COLUMNS": str(_NARROW_WIDTH)})
    wide = make_display_context(env={"COLUMNS": str(_WIDE_WIDTH)})
    assert compact.condenser_soft_limit < _WIDE_CONDENSER_SOFT
    assert wide.condenser_soft_limit == _WIDE_CONDENSER_SOFT


def test_force_mode_overrides_width_detection() -> None:
    """force_mode='compact' should override even a very wide COLUMNS value."""
    ctx = make_display_context(env={"COLUMNS": "300"}, force_mode="compact")
    assert ctx.mode == "compact"
    assert ctx.narrow is True


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
    assert ctx.mode == "wide"


def test_compact_limits_are_smaller_than_wide() -> None:
    """All adaptive limits in compact mode must be ≤ the corresponding wide-mode values."""
    compact = make_display_context(env={"COLUMNS": "40"})
    wide = make_display_context(env={"COLUMNS": "200"})
    assert compact.headline_max_chars <= wide.headline_max_chars
    assert compact.condenser_soft_limit <= wide.condenser_soft_limit
    assert compact.condenser_hard_limit <= wide.condenser_hard_limit
    assert compact.streaming_checkpoint_chars <= wide.streaming_checkpoint_chars
    assert compact.thinking_preview_min_chars <= wide.thinking_preview_min_chars
    assert compact.tool_result_headline_min_chars <= wide.tool_result_headline_min_chars


def test_medium_limits_are_between_compact_and_wide() -> None:
    """All adaptive limits in medium mode must fall between compact and wide."""
    compact = make_display_context(env={"COLUMNS": "40"})
    medium = make_display_context(env={"COLUMNS": str(_MEDIUM_WIDTH)})
    wide = make_display_context(env={"COLUMNS": "200"})
    assert compact.headline_max_chars <= medium.headline_max_chars <= wide.headline_max_chars
    assert compact.condenser_soft_limit <= medium.condenser_soft_limit <= wide.condenser_soft_limit
    assert compact.condenser_hard_limit <= medium.condenser_hard_limit <= wide.condenser_hard_limit
    assert (
        compact.streaming_checkpoint_chars
        <= medium.streaming_checkpoint_chars
        <= wide.streaming_checkpoint_chars
    )


def test_threshold_60_gives_medium_mode() -> None:
    """Width exactly 60 should yield medium mode (lower boundary)."""
    ctx = make_display_context(env={"COLUMNS": "60"})
    assert ctx.mode == "medium"
    assert ctx.narrow is False


def test_threshold_99_gives_medium_mode() -> None:
    """Width 99 should yield medium mode (upper boundary, just below wide)."""
    ctx = make_display_context(env={"COLUMNS": "99"})
    assert ctx.mode == "medium"
    assert ctx.narrow is False


def test_threshold_100_gives_wide_mode() -> None:
    """Width 100 should yield wide mode (lower boundary of wide)."""
    ctx = make_display_context(env={"COLUMNS": "100"})
    assert ctx.mode == "wide"
    assert ctx.narrow is False


def test_ralph_force_narrow_truthy_variants() -> None:
    """All truthy variants of RALPH_FORCE_NARROW should force compact mode."""
    for val in ("1", "true", "yes", "on", "TRUE", "True", "YES", "ON"):
        ctx = make_display_context(env={"RALPH_FORCE_NARROW": val, "COLUMNS": "200"})
        assert ctx.mode == "compact", f"RALPH_FORCE_NARROW={val!r} should yield compact"


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
