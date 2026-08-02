"""Regression coverage for the production display colour default."""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console
from rich.text import Text

from ralph.display.context import make_display_context
from ralph.display.theme import make_console


def _colour_env() -> dict[str, str]:
    return {"TERM": "xterm-256color", "COLORTERM": "truecolor"}


def test_default_console_emits_themed_truecolor(monkeypatch: pytest.MonkeyPatch) -> None:
    """The production default must not pass Rich's colour-disabling None."""
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")
    for name in ("NO_COLOR", "FORCE_COLOR", "TTY_COMPATIBLE"):
        monkeypatch.delenv(name, raising=False)

    stream = StringIO()
    console = make_console(file=stream, force_terminal=True)
    console.print(Text("coloured", style="theme.status.success"))

    assert console.color_system == "truecolor"
    assert "38;2;" in stream.getvalue()


def test_display_context_default_console_keeps_colour_enabled() -> None:
    """The context's production construction path preserves Rich styling."""
    ctx = make_display_context(console=None, env=_colour_env())

    assert ctx.console.color_system is not None
    assert ctx.console.no_color is False


def test_display_context_regression_runtime_environment_keeps_colour_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2: production env resolution must never silently disable ANSI colour."""
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")
    for name in ("NO_COLOR", "FORCE_COLOR"):
        monkeypatch.delenv(name, raising=False)

    ctx = make_display_context()

    assert ctx.console.color_system is not None
    assert ctx.color_enabled is True


def test_make_console_regression_none_override_still_enables_colour() -> None:
    """S-2: an omitted colour system must resolve to Rich's auto mode, not None."""
    console = make_console(force_terminal=True, color_system=None)

    assert console.color_system is not None


@pytest.mark.parametrize(
    "env",
    (
        _colour_env(),
        {**_colour_env(), "RALPH_TERMINAL_BG": "dark"},
        {**_colour_env(), "RALPH_TERMINAL_BG": "light"},
    ),
)
def test_display_context_regression_injected_console_keeps_semantic_theme(
    env: dict[str, str],
) -> None:
    """S-2: injected consoles render semantic styles on every background path."""
    stream = StringIO()
    ctx = make_display_context(
        console=Console(file=stream, force_terminal=True, color_system="truecolor", highlight=False),
        env=env,
    )

    assert ctx.console.get_style("theme.status.success").color is not None
    ctx.console.print(Text("coloured", style="theme.status.success"))
    assert "38;2;" in stream.getvalue()


def test_display_context_regression_unknown_background_themes_injected_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2: no detected background must still emit semantic truecolor output."""
    monkeypatch.setattr(
        "ralph.display.context.detect_terminal_background_is_light",
        lambda _env: None,
    )
    stream = StringIO()
    ctx = make_display_context(
        console=Console(file=stream, force_terminal=True, color_system="truecolor", highlight=False),
        env={},
    )

    assert ctx.console.get_style("theme.status.success").color is not None
    ctx.console.print(Text("coloured", style="theme.status.success"))
    assert "38;2;" in stream.getvalue()


def test_display_context_regression_standard_console_does_not_downgrade_later_truecolor() -> None:
    """DA-001: one ANSI console cannot poison later truecolor semantic output."""
    standard_stream = StringIO()
    standard_context = make_display_context(
        console=Console(
            file=standard_stream,
            force_terminal=True,
            color_system="standard",
            highlight=False,
        ),
        env={},
    )
    standard_context.console.print(Text("development", style="theme.phase.development"))

    truecolor_stream = StringIO()
    truecolor_console = make_console(file=truecolor_stream, force_terminal=True)
    truecolor_console.print(Text("development", style="theme.phase.development"))

    assert "38;2;" in truecolor_stream.getvalue()


def test_no_color_still_disables_output() -> None:
    """The explicit standard disable switch remains authoritative."""
    env = {**_colour_env(), "NO_COLOR": "1"}
    ctx = make_display_context(console=None, env=env)
    stream = StringIO()
    console = make_console(file=stream, force_terminal=True, no_color=ctx.console.no_color)
    console.print(Text("plain", style="theme.status.success"))

    assert ctx.console.no_color is True
    assert "38;2;" not in stream.getvalue()
