"""Mutation-style visual-floor checks for console palettes and previews."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

import pytest
from pygments.token import Comment, Generic, Keyword, Name, Number, Operator, Punctuation, String

from ralph.display import theme
from ralph.display.edit_preview import build_edit_preview
from ralph.display.scene_catalog import CONTRAST_FLOOR
from ralph.display.theme import preview_background_for_background
from ralph.syntax_theme import SyntaxThemes

_PREVIEW_SURFACES = {
    False: preview_background_for_background(False),
    True: preview_background_for_background(True),
}

_REQUIRED_TOKENS = (
    Comment,
    Keyword,
    Name,
    Name.Function,
    String,
    Number,
    Operator,
    Punctuation,
    Generic.Deleted,
    Generic.Inserted,
)


def _assert_palette_contrast(
    styles: Mapping[str, tuple[str, str, str]], backgrounds: tuple[str, ...]
) -> None:
    failures: list[str] = []
    for role, (style, _glyph, _label) in styles.items():
        foreground = theme._extract_hex(style)
        if not foreground:
            failures.append(f"{role}: no fixed foreground")
            continue
        for background in backgrounds:
            ratio = theme.contrast_ratio(foreground, background)
            if ratio < CONTRAST_FLOOR:
                failures.append(f"{role}: {ratio:.2f}")
    if failures:
        raise AssertionError(", ".join(failures))


def _assert_complete_token_classes(style_type: type[object]) -> None:
    styles = style_type.styles
    missing = [token for token in _REQUIRED_TOKENS if token not in styles or not styles[token]]
    if missing:
        raise AssertionError(f"missing syntax token classes: {missing}")


def test_visual_floor_semantic_palettes_use_fixed_contrast_safe_foregrounds() -> None:
    _assert_palette_contrast(theme.STATUS_STYLES, ("#000000",))
    _assert_palette_contrast(theme.STATUS_STYLES_ON_LIGHT_BG, ("#FFFFFF",))
    _assert_palette_contrast(theme.STATUS_STYLES_ON_UNKNOWN_BG, ("#000000", "#FFFFFF"))


def test_visual_floor_theme_roles_never_recede_to_attribute_only_dim() -> None:
    """S-3: semantic chrome retains an identifiable hue, not dim-only styling."""
    semantic_roles = (
        "theme.cat.meta",
        "theme.text.muted",
        "theme.status.bar_marker",
        "theme.status.path_marker",
        "theme.status.path",
    )
    for role in semantic_roles:
        assert theme._extract_hex(theme._THEME_STYLES[role]), role


def test_visual_floor_bad_palette_fixture_is_rejected() -> None:
    bad = dict(theme.STATUS_STYLES)
    bad["waiting"] = ("dim", "?", "WAIT")
    with pytest.raises(AssertionError, match="no fixed foreground"):
        _assert_palette_contrast(bad, ("#000000",))


def test_visual_floor_syntax_theme_preserves_complete_token_range() -> None:
    for style_type in (SyntaxThemes.dark(), SyntaxThemes.light(), SyntaxThemes.unknown()):
        _assert_complete_token_classes(style_type)


def test_visual_floor_syntax_tokens_clear_contrast_on_their_owned_preview_surface() -> None:
    """S-4: every fixed syntax foreground clears 4.5:1 on its actual fill."""
    for background, style_type in ((False, SyntaxThemes.dark()), (True, SyntaxThemes.light())):
        foregrounds = {
            foreground
            for color in style_type.styles.values()
            if isinstance(color, str) and (foreground := theme._extract_hex(color))
        }
        assert foregrounds
        surface = _PREVIEW_SURFACES[background]
        assert all(
            theme.contrast_ratio(foreground, surface) >= CONTRAST_FLOOR
            for foreground in foregrounds
        )


def test_visual_floor_bad_syntax_foreground_fixture_is_rejected() -> None:
    class BadStyle:
        styles: ClassVar[dict[object, str]] = {Comment: "#222222"}

    with pytest.raises(AssertionError):
        assert all(
            theme.contrast_ratio(foreground, _PREVIEW_SURFACES[False]) >= CONTRAST_FLOOR
            for foreground in BadStyle.styles.values()
        )


def test_visual_floor_known_background_previews_paint_one_complete_owned_surface() -> None:
    """S-4: known terminal backgrounds give source rows and gutters one owned fill."""
    for background in (False, True):
        preview = build_edit_preview(
            "write_file",
            {"path": "example.py", "content": "def render() -> int:\n    return 1\n"},
            width=80,
            terminal_bg_is_light=background,
        )
        assert preview is not None
        assert getattr(preview, "background_color", None) == preview_background_for_background(
            background
        )
    assert preview_background_for_background(None) == "default"


def test_visual_floor_missing_token_fixture_is_rejected() -> None:
    class BadStyle:
        styles: ClassVar[dict[object, str]] = {Comment: "#ffffff"}

    with pytest.raises(AssertionError, match="missing syntax token classes"):
        _assert_complete_token_classes(BadStyle)
