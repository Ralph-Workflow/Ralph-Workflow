from __future__ import annotations

import importlib

import pytest
from rich.console import Console
from rich.theme import Theme

from ralph.display.context import make_display_context

theme = importlib.import_module("ralph.display.theme")

DEFAULT_WIDTH = 80


def test_okabe_ito_constants_match_canonical_hex() -> None:
    assert theme.ORANGE == "#E69F00"
    assert theme.SKY_BLUE == "#56B4E9"
    assert theme.BLUISH_GREEN == "#009E73"
    assert theme.YELLOW == "#F0E442"
    assert theme.BLUE == "#0072B2"
    assert theme.VERMILLION == "#D55E00"
    assert theme.REDDISH_PURPLE == "#CC79A7"
    assert theme.BLACK == "#000000"


def test_status_styles_cover_expected_statuses() -> None:
    assert set(theme.STATUS_STYLES) == {
        "success",
        "running",
        "warning",
        "error",
        "skipped",
        "pending",
        "info",
    }
    assert theme.STATUS_STYLES["info"] == ("#0072B2", "\u2139", "INFO")


def test_format_status_returns_marked_up_label() -> None:
    rendered = theme.format_status("success")

    assert "✓" in rendered
    assert "PASS" in rendered
    assert "#009E73" in rendered


def test_format_status_unknown_status_raises_key_error() -> None:
    with pytest.raises(KeyError):
        theme.format_status("nonexistent")


def test_make_console_respects_explicit_no_color() -> None:
    console = theme.make_console(no_color=True, width=DEFAULT_WIDTH)

    assert console.no_color is True
    assert console.width == DEFAULT_WIDTH
    assert isinstance(console, Console)


def test_make_console_respects_no_color_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """make_display_context respects NO_COLOR env var and propagates to console.no_color."""
    monkeypatch.setenv("NO_COLOR", "1")

    ctx = make_display_context()

    assert ctx.console.no_color is True
    assert ctx.color_enabled is False


def test_make_console_prefers_no_color_over_force_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """NO_COLOR takes precedence over FORCE_COLOR in make_display_context."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")

    ctx = make_display_context()

    assert ctx.console.no_color is True
    assert ctx.color_enabled is False


def test_ralph_theme_contains_required_style_keys() -> None:
    assert isinstance(theme.RALPH_THEME, Theme)
    assert {
        "theme.status.success",
        "theme.status.running",
        "theme.status.warning",
        "theme.status.error",
        "theme.status.skipped",
        "theme.status.pending",
        "theme.status.info",
        "theme.phase.planning",
        "theme.phase.development",
        "theme.phase.review",
        "theme.phase.fix",
        "theme.phase.commit",
        "theme.phase.complete",
        "theme.phase.failed",
        "theme.panel.border",
        "theme.panel.title",
        "theme.text.muted",
        "theme.text.emphasis",
    }.issubset(set(theme.RALPH_THEME.styles))
