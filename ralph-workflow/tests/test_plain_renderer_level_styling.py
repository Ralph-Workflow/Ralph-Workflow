from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.theme import theme_for_background

_RETIRED_LEVEL_BADGES = ("INFO", "WARN", "ERROR", "SUCCESS", "MILESTONE")
_RETIRED_CAT_BADGES = ("META", "OUT")


def _make_color_renderer() -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(
        file=buf,
        color_system="truecolor",
        force_terminal=True,
        no_color=False,
        theme=theme_for_background(True),
        width=200,
        highlight=False,
    )
    return ParallelDisplay(
        make_display_context(console=console, env={"RALPH_TERMINAL_BG": "light"})
    ), buf


def _make_plain_renderer() -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(
        file=buf,
        color_system=None,
        force_terminal=False,
        width=200,
        highlight=False,
    )
    return ParallelDisplay(make_display_context(console=console, env={})), buf


def _emit(renderer: ParallelDisplay, kind: str, body: str) -> None:
    renderer.start()
    renderer.emit_activity_line("u", kind, body)
    renderer.stop()


@pytest.mark.parametrize(
    "kind", ["text", "thinking", "tool_use", "tool_result", "error", "progress"]
)
def test_activity_line_chrome_carries_no_level_badge_plain(kind: str) -> None:
    renderer, buf = _make_plain_renderer()
    _emit(renderer, kind, "msg")
    out = buf.getvalue()
    for forbidden in _RETIRED_LEVEL_BADGES:
        assert forbidden not in out, (
            f"wt-028-display S-4: {kind!r} line must not carry "
            f"{forbidden!r} LEVEL chrome; got: {out!r}"
        )


@pytest.mark.parametrize(
    "kind", ["text", "thinking", "tool_use", "tool_result", "error", "progress"]
)
def test_activity_line_chrome_carries_no_cat_badge_plain(kind: str) -> None:
    renderer, buf = _make_plain_renderer()
    _emit(renderer, kind, "msg")
    out = buf.getvalue()
    for forbidden in _RETIRED_CAT_BADGES:
        assert forbidden not in out, (
            f"wt-028-display S-4: {kind!r} line must not carry "
            f"{forbidden!r} CAT chrome; got: {out!r}"
        )


def test_activity_line_body_survives_with_tag_and_unit() -> None:
    renderer, buf = _make_plain_renderer()
    _emit(renderer, "text", "hello world")
    out = buf.getvalue()
    assert "[output][u]" in out, f"[output][u] tag must survive: {out!r}"
    assert "hello world" in out, f"body must survive: {out!r}"


def test_activity_line_keeps_no_plumbing_chrome_under_truecolor() -> None:
    renderer, buf = _make_color_renderer()
    _emit(renderer, "error", "bad")
    out = buf.getvalue()
    assert "38;2;" in out, f"activity carriers must be semantically coloured: {out!r}"
    for forbidden in _RETIRED_LEVEL_BADGES + _RETIRED_CAT_BADGES:
        assert forbidden not in out, (
            f"wt-028-display S-4: error line must not carry {forbidden!r} "
            f"chrome even under color; got: {out!r}"
        )


@pytest.mark.parametrize("kind", ["text", "thinking", "tool_use", "tool_result", "error"])
def test_activity_lines_have_nondefault_truecolor_carriers(kind: str) -> None:
    renderer, buf = _make_color_renderer()
    _emit(renderer, kind, "visible activity body")
    out = buf.getvalue()
    assert out.count("38;2;") >= 4, (
        f"{kind} row must colour timestamp, tag, identity, and body independently: {out!r}"
    )
    assert "[" in out and "visible activity body" in out


def test_activity_line_plain_output_has_no_ansi() -> None:
    renderer, buf = _make_plain_renderer()
    _emit(renderer, "progress", "50%")
    out = buf.getvalue()
    assert "\x1b[" not in out, f"plain renderer must not emit ANSI; got: {out!r}"
