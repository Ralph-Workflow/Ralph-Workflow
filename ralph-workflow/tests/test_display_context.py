from __future__ import annotations

import re
from io import StringIO

import pytest
from rich.console import Console

from ralph.display import _mode_adaptive_limits as limits_module
from ralph.display import context as context_module
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.theme import RALPH_THEME, theme_for_background

_NARROW_WIDTH = 50
_MEDIUM_WIDTH = 80
_WIDE_WIDTH = 120


def _recording_console(width: int = _WIDE_WIDTH) -> Console:
    return Console(file=StringIO(), force_terminal=False, width=width, theme=RALPH_THEME)


def test_default_context_has_themed_console_and_positive_width() -> None:
    ctx = make_display_context(env={})
    assert isinstance(ctx, DisplayContext)
    assert ctx.width > 0
    assert ctx.console is not None
    expected = theme_for_background(ctx.terminal_background_is_light)
    assert set(ctx.theme.styles) == set(expected.styles)
    assert {name: str(style) for name, style in ctx.theme.styles.items()} == {
        name: str(style) for name, style in expected.styles.items()
    }


@pytest.mark.parametrize("columns", ["40", "50", "60", "80", "99", "100", "120", "200", "300"])
def test_any_columns_width_gives_default_mode(columns: str) -> None:
    ctx = make_display_context(env={"COLUMNS": columns})
    assert ctx.width == int(columns)
    mode_attr = vars(ctx)["mode"]
    assert mode_attr == "default", (
        f"DisplayContext.mode must be 'default' at any COLUMNS width; "
        f"got {mode_attr!r} at width {ctx.width}"
    )


@pytest.mark.parametrize("columns", ["40", "50", "60", "80", "99", "100", "120", "200", "300"])
def test_any_columns_width_adaptive_limits_are_constant(columns: str) -> None:
    ctx = make_display_context(env={"COLUMNS": columns})
    assert ctx.headline_max_chars == 120
    assert ctx.condenser_soft_limit == 400
    assert ctx.condenser_hard_limit == 4000
    assert ctx.thinking_preview_min_chars == 80
    assert ctx.tool_result_headline_min_chars == 80


def test_default_limits_owner_is_mode_adaptive_limits_module() -> None:
    assert limits_module._DEFAULT_LIMITS is context_module._DEFAULT_LIMITS, (
        "_DEFAULT_LIMITS must be a single shared object identity owned by "
        "ralph.display._mode_adaptive_limits and consumed by ralph.display.context."
    )


def test_no_color_env_disables_color() -> None:
    ctx = make_display_context(env={"NO_COLOR": "1", "COLUMNS": str(_WIDE_WIDTH)})
    assert ctx.color_enabled is False


def test_display_context_regression_force_color_redirect_emits_semantic_ansi() -> None:
    stream = StringIO()
    ctx = make_display_context(
        env={"FORCE_COLOR": "1", "RALPH_TERMINAL_BG": "dark"},
        output_stream=stream,
    )

    ctx.console.print("[theme.status.success]PASS[]")

    assert ctx.color_enabled is True
    assert re.search(r"\x1b\[[0-9;]*38;2;", stream.getvalue())


class _TtyLikeStringIO(StringIO):
    def isatty(self) -> bool:
        return True


def test_display_context_regression_normal_tty_emits_semantic_ansi() -> None:
    stream = _TtyLikeStringIO()
    ctx = make_display_context(
        env={"RALPH_TERMINAL_BG": "dark"},
        output_stream=stream,
    )

    ctx.console.print("[theme.phase.development]development[/]")

    assert ctx.color_enabled is True
    assert re.search(r"\x1b\[[0-9;]*38;2;", stream.getvalue())


def test_output_stream_cannot_override_an_injected_console() -> None:
    with pytest.raises(ValueError, match="output_stream cannot be combined"):
        make_display_context(
            env={},
            console=_recording_console(),
            output_stream=StringIO(),
        )


def test_display_context_regression_empty_colour_overrides_are_ignored() -> None:
    no_color = make_display_context(
        env={"NO_COLOR": "", "COLUMNS": str(_WIDE_WIDTH)},
        console=Console(file=StringIO(), force_terminal=True, width=_WIDE_WIDTH, theme=RALPH_THEME),
    )
    force_color = make_display_context(
        env={"FORCE_COLOR": "", "COLUMNS": str(_WIDE_WIDTH)},
        console=Console(file=StringIO(), force_terminal=True, width=_WIDE_WIDTH, theme=RALPH_THEME),
    )
    assert no_color.color_enabled is True
    assert force_color.color_enabled is True


def test_no_color_wins_over_force_color() -> None:
    env = {"NO_COLOR": "1", "FORCE_COLOR": "1", "COLUMNS": str(_WIDE_WIDTH)}
    ctx = make_display_context(env=env)
    assert ctx.color_enabled is False


def test_injected_console_is_reused() -> None:
    console = _recording_console(width=200)
    ctx = make_display_context(env={"COLUMNS": "200"}, console=console)
    assert ctx.console is console


_FORCE_WIDTH = 150


def test_force_width_overrides_columns_env() -> None:
    ctx = make_display_context(env={"COLUMNS": str(_NARROW_WIDTH)}, force_width=_FORCE_WIDTH)
    assert ctx.width == _FORCE_WIDTH


def test_default_mode_uses_single_fixed_limits() -> None:
    narrow = make_display_context(env={"COLUMNS": str(_NARROW_WIDTH)})
    medium = make_display_context(env={"COLUMNS": str(_MEDIUM_WIDTH)})
    wide = make_display_context(env={"COLUMNS": str(_WIDE_WIDTH)})
    assert narrow.headline_max_chars == medium.headline_max_chars == wide.headline_max_chars
    assert narrow.condenser_soft_limit == medium.condenser_soft_limit == wide.condenser_soft_limit
    assert narrow.condenser_hard_limit == medium.condenser_hard_limit == wide.condenser_hard_limit


def test_injected_no_color_console_disables_color() -> None:
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
