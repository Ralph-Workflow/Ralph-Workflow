from __future__ import annotations

import importlib

import pytest
from rich.console import Console
from rich.style import Style
from rich.theme import Theme

from ralph.display.context import make_display_context

theme = importlib.import_module("ralph.display.theme")

DEFAULT_WIDTH = 80


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
    expected_info_color = theme.pick_status_styles(False)["info"][0]
    assert theme.STATUS_STYLES["info"] == (expected_info_color, "\u2139", "INFO")


def test_structural_chrome_roles_no_longer_borrow_the_info_state_hex() -> None:
    """E-2: panel/banner/emphasis/milestone/outer_dev chrome and the commit,
    development_commit, planning, and review_commit phase labels must no
    longer resolve to the same hex as the literal semantic info state --
    they are structural (tier 2), not a state carrier."""
    palettes = {False: theme._pal_dark, True: theme._pal_light, None: theme._pal_unknown}
    for terminal_bg_is_light, palette in palettes.items():
        styles = theme._build_theme_styles(palette)
        info_hex = theme._extract_hex(styles["theme.log.info"])
        chrome_keys = (
            "theme.panel.border",
            "theme.panel.title",
            "theme.banner.ascii",
            "theme.banner.border",
            "theme.banner.title",
            "theme.banner.welcome",
            "theme.text.emphasis",
            "theme.level.milestone",
            "theme.log.milestone",
            "theme.outer_dev",
            "theme.phase.commit",
            "theme.phase.development_commit",
            "theme.phase.planning",
            "theme.phase.review_commit",
        )
        for key in chrome_keys:
            chrome_hex = theme._extract_hex(styles[key])
            assert chrome_hex, key
            assert chrome_hex != info_hex, (terminal_bg_is_light, key, chrome_hex, info_hex)


def test_format_status_returns_marked_up_label() -> None:
    rendered = theme.format_status("success")

    assert "✓" in rendered
    assert "PASS" in rendered
    assert theme.pick_status_styles(False)["success"][0] in rendered


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


def test_make_console_regression_default_color_auto_detection_respects_no_color() -> None:
    """S-2: default color detection emits truecolor while no_color wins."""
    color_console = theme.make_console(force_terminal=True)
    no_color_console = theme.make_console(force_terminal=True, no_color=True)

    with color_console.capture() as color_capture:
        color_console.print("[theme.phase.development]development[/]")
    with no_color_console.capture() as no_color_capture:
        no_color_console.print("[theme.phase.development]development[/]")

    assert "\x1b[38;2;" in color_capture.get()
    assert "\x1b[38;2;" not in no_color_capture.get()


@pytest.mark.parametrize(
    "styles",
    (theme._THEME_STYLES, theme._THEME_STYLES_ON_LIGHT_BG, theme._THEME_STYLES_ON_UNKNOWN_BG),
)
def test_theme_regression_fresh_styles_preserve_rich_attributes(styles: dict[str, str]) -> None:
    """S-6: per-console style rebuilding must preserve every supported style attribute."""
    for style in styles.values():
        expected = Style.parse(style)
        actual = theme._fresh_style(style)
        assert (
            actual.color,
            actual.bold,
            actual.italic,
            actual.dim,
            actual.underline,
            actual.reverse,
            actual.bgcolor,
            actual.strike,
        ) == (
            expected.color,
            expected.bold,
            expected.italic,
            expected.dim,
            expected.underline,
            expected.reverse,
            expected.bgcolor,
            expected.strike,
        )


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
