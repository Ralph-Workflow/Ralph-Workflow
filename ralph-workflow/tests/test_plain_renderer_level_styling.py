"""Tests for PlainLogRenderer level and category badge styling."""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from ralph.display.plain_renderer import PlainLogRenderer
from ralph.display.theme import RALPH_THEME


def _make_color_renderer() -> tuple[PlainLogRenderer, StringIO]:
    buf = StringIO()
    console = Console(
        file=buf,
        color_system="truecolor",
        force_terminal=True,
        no_color=False,
        theme=RALPH_THEME,
        width=200,
        highlight=False,
    )
    return PlainLogRenderer(console), buf


def _make_plain_renderer() -> tuple[PlainLogRenderer, StringIO]:
    buf = StringIO()
    console = Console(
        file=buf,
        color_system=None,
        force_terminal=False,
        width=200,
        highlight=False,
    )
    return PlainLogRenderer(console), buf


def test_level_badge_produces_ansi_when_color_enabled() -> None:
    renderer, buf = _make_color_renderer()
    renderer.emit_activity_line("u", "error", "bad")
    out = buf.getvalue()
    assert "\x1b[" in out
    assert "ERROR" in out


def test_level_badge_plain_when_color_system_none() -> None:
    renderer, buf = _make_plain_renderer()
    renderer.emit_activity_line("u", "error", "bad")
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "ERROR" in out


@pytest.mark.parametrize(
    ("kind", "expected_level"),
    [
        ("tool_result", "SUCCESS"),
        ("lifecycle", "MILESTONE"),
        ("error", "ERROR"),
        ("progress", "INFO"),
    ],
)
def test_level_badge_text_present_plain(kind: str, expected_level: str) -> None:
    renderer, buf = _make_plain_renderer()
    renderer.emit_activity_line("u", kind, "msg")
    out = buf.getvalue()
    assert expected_level in out


@pytest.mark.parametrize(
    ("kind", "expected_level"),
    [
        ("tool_result", "SUCCESS"),
        ("lifecycle", "MILESTONE"),
        ("error", "ERROR"),
        ("progress", "INFO"),
    ],
)
def test_level_badge_produces_ansi_for_all_levels(kind: str, expected_level: str) -> None:
    renderer, buf = _make_color_renderer()
    renderer.emit_activity_line("u", kind, "msg")
    out = buf.getvalue()
    assert "\x1b[" in out
    assert expected_level in out


def test_cat_badge_meta_present_on_tty() -> None:
    renderer, buf = _make_color_renderer()
    renderer.emit_activity_line("u", "progress", "50%")
    out = buf.getvalue()
    assert "\x1b[" in out
    assert "META" in out


def test_cat_badge_cont_present_on_tty() -> None:
    renderer, buf = _make_color_renderer()
    renderer.emit_activity_line("u", "raw", "data")
    out = buf.getvalue()
    assert "\x1b[" in out
    assert "CONT" in out


def test_cat_badge_plain_when_color_system_none() -> None:
    renderer, buf = _make_plain_renderer()
    renderer.emit_activity_line("u", "progress", "50%")
    out = buf.getvalue()
    assert "\x1b[" not in out
    assert "META" in out
