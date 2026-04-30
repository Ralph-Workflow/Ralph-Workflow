"""Tests for DisplayContext and make_display_context factory."""

from __future__ import annotations

from rich.console import Console

from ralph.display import DisplayContext as DisplayContextExport
from ralph.display import make_display_context as make_display_context_export
from ralph.display.context import (
    _COMPACT_CONDENSER_HARD_LIMIT,
    _COMPACT_CONDENSER_SOFT_LIMIT,
    _COMPACT_HEADLINE_MAX_CHARS,
    _COMPACT_STREAMING_CHECKPOINT_CHARS,
    _COMPACT_THINKING_PREVIEW_MIN_CHARS,
    _COMPACT_TOOL_RESULT_HEADLINE_MIN_CHARS,
    _WIDE_CONDENSER_HARD_LIMIT,
    _WIDE_CONDENSER_SOFT_LIMIT,
    _WIDE_HEADLINE_MAX_CHARS,
    _WIDE_STREAMING_CHECKPOINT_CHARS,
    _WIDE_THINKING_PREVIEW_MIN_CHARS,
    _WIDE_TOOL_RESULT_HEADLINE_MIN_CHARS,
    DisplayContext,
    make_display_context,
)
from ralph.display.mode import NARROW_THRESHOLD
from ralph.display.theme import RALPH_THEME

_NARROW_TEST_WIDTH = 40
_WIDE_TEST_WIDTH = 200


def test_wide_mode_for_wide_terminal() -> None:
    console = Console(width=120, force_terminal=True)
    ctx = make_display_context(console=console, env={})
    assert ctx.mode == "wide"
    assert ctx.narrow is False


def test_compact_mode_for_narrow_terminal() -> None:
    console = Console(width=40, force_terminal=True)
    ctx = make_display_context(console=console, env={})
    assert ctx.mode == "compact"
    assert ctx.narrow is True


def test_compact_mode_at_threshold_boundary() -> None:
    # width == NARROW_THRESHOLD (60) is not < 60 → falls into medium tier
    console = Console(width=NARROW_THRESHOLD, force_terminal=True)
    ctx = make_display_context(console=console, env={})
    assert ctx.mode == "medium"


def test_ralph_force_narrow_env_forces_compact() -> None:
    console = Console(width=200, force_terminal=True)
    ctx = make_display_context(console=console, env={"RALPH_FORCE_NARROW": "1"})
    assert ctx.mode == "compact"
    assert ctx.narrow is True


def test_ralph_force_narrow_true_forces_compact() -> None:
    console = Console(width=200, force_terminal=True)
    ctx = make_display_context(console=console, env={"RALPH_FORCE_NARROW": "true"})
    assert ctx.mode == "compact"


def test_ralph_force_narrow_zero_does_not_force_compact() -> None:
    console = Console(width=200, force_terminal=True)
    ctx = make_display_context(console=console, env={"RALPH_FORCE_NARROW": "0"})
    assert ctx.mode == "wide"


def test_force_mode_overrides_width() -> None:
    console = Console(width=200, force_terminal=True)
    ctx = make_display_context(console=console, env={}, force_mode="compact")
    assert ctx.mode == "compact"
    assert ctx.narrow is True
    # Limits should be compact, not wide
    assert ctx.headline_max_chars == _COMPACT_HEADLINE_MAX_CHARS


def test_force_width_overrides_console_width() -> None:
    console = Console(width=120, force_terminal=True)
    ctx = make_display_context(console=console, env={}, force_width=_NARROW_TEST_WIDTH)
    assert ctx.width == _NARROW_TEST_WIDTH
    assert ctx.mode == "compact"


def test_columns_env_overrides_console_width() -> None:
    console = Console(width=120, force_terminal=True)
    ctx = make_display_context(console=console, env={"COLUMNS": str(_NARROW_TEST_WIDTH)})
    assert ctx.width == _NARROW_TEST_WIDTH
    assert ctx.mode == "compact"


def test_force_width_takes_precedence_over_columns_env() -> None:
    console = Console(width=120, force_terminal=True)
    ctx = make_display_context(
        console=console, env={"COLUMNS": str(_NARROW_TEST_WIDTH)}, force_width=_WIDE_TEST_WIDTH
    )
    assert ctx.width == _WIDE_TEST_WIDTH
    assert ctx.mode == "wide"


def test_no_color_env_disables_color() -> None:
    console = Console(width=120)
    ctx = make_display_context(console=console, env={"NO_COLOR": "1"})
    assert ctx.color_enabled is False


def test_color_enabled_by_default() -> None:
    console = Console(width=120)
    ctx = make_display_context(console=console, env={})
    assert ctx.color_enabled is True


def test_wide_mode_limits() -> None:
    console = Console(width=120, force_terminal=True)
    ctx = make_display_context(console=console, env={})
    assert ctx.headline_max_chars == _WIDE_HEADLINE_MAX_CHARS
    assert ctx.condenser_soft_limit == _WIDE_CONDENSER_SOFT_LIMIT
    assert ctx.condenser_hard_limit == _WIDE_CONDENSER_HARD_LIMIT
    assert ctx.streaming_checkpoint_chars == _WIDE_STREAMING_CHECKPOINT_CHARS
    assert ctx.thinking_preview_min_chars == _WIDE_THINKING_PREVIEW_MIN_CHARS
    assert ctx.tool_result_headline_min_chars == _WIDE_TOOL_RESULT_HEADLINE_MIN_CHARS


def test_compact_mode_limits() -> None:
    console = Console(width=40, force_terminal=True)
    ctx = make_display_context(console=console, env={})
    assert ctx.headline_max_chars == _COMPACT_HEADLINE_MAX_CHARS
    assert ctx.condenser_soft_limit == _COMPACT_CONDENSER_SOFT_LIMIT
    assert ctx.condenser_hard_limit == _COMPACT_CONDENSER_HARD_LIMIT
    assert ctx.streaming_checkpoint_chars == _COMPACT_STREAMING_CHECKPOINT_CHARS
    assert ctx.thinking_preview_min_chars == _COMPACT_THINKING_PREVIEW_MIN_CHARS
    assert ctx.tool_result_headline_min_chars == _COMPACT_TOOL_RESULT_HEADLINE_MIN_CHARS


def test_compact_limits_smaller_than_wide() -> None:
    assert _COMPACT_HEADLINE_MAX_CHARS < _WIDE_HEADLINE_MAX_CHARS
    assert _COMPACT_CONDENSER_SOFT_LIMIT < _WIDE_CONDENSER_SOFT_LIMIT
    assert _COMPACT_CONDENSER_HARD_LIMIT < _WIDE_CONDENSER_HARD_LIMIT
    assert _COMPACT_STREAMING_CHECKPOINT_CHARS < _WIDE_STREAMING_CHECKPOINT_CHARS
    assert _COMPACT_THINKING_PREVIEW_MIN_CHARS <= _WIDE_THINKING_PREVIEW_MIN_CHARS
    assert _COMPACT_TOOL_RESULT_HEADLINE_MIN_CHARS <= _WIDE_TOOL_RESULT_HEADLINE_MIN_CHARS


def test_display_context_is_frozen() -> None:
    params = getattr(DisplayContext, "__dataclass_params__", None)
    assert params is not None and getattr(params, "frozen", False) is True


def test_display_context_has_ralph_theme() -> None:
    ctx = make_display_context(env={})
    assert ctx.theme is RALPH_THEME


def test_make_display_context_creates_console_when_none() -> None:
    ctx = make_display_context(env={})
    assert ctx.console is not None
    assert isinstance(ctx.width, int)
    assert ctx.width > 0


def test_console_passed_in_is_used() -> None:
    console = Console(width=80)
    ctx = make_display_context(console=console, env={})
    assert ctx.console is console


def test_display_context_exported_from_display_package() -> None:
    assert DisplayContextExport is DisplayContext
    assert make_display_context_export is make_display_context
