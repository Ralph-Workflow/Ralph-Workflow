"""Characterization tests: syntax token roles follow Monokai Pro's own scope
convention (PLAN.md S-4), instead of the arbitrary role assignment the
generator machinery inherited (Characterize point 2).
"""

from __future__ import annotations

import pytest
from pygments.token import (
    Comment,
    Error,
    Generic,
    Keyword,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Text,
)

from ralph.display._palette import ROLE_ANCHORS, hex_to_rgb, oklab_to_oklch, rgb_to_oklab
from ralph.syntax_theme import SyntaxThemes

_NEUTRAL_ROLES = frozenset({"foreground", "comment"})

# token label -> expected semantic role, per the S-4 mapping table.
_TOKEN_ROLE: dict[str, str] = {
    "Text": "foreground",
    "Name": "foreground",
    "Comment": "comment",
    "Keyword": "error",
    "Operator": "error",
    "Punctuation": "error",
    "String": "skipped",
    "Number": "pending",
    "Name.Function": "success",
    "Name.Attribute": "warning",
    "Keyword.Type": "info",
    "Generic.Subheading": "info",
    "Token.Error": "error",
}

_TOKEN_BY_LABEL: dict[str, object] = {
    "Text": Text,
    "Name": Name,
    "Comment": Comment,
    "Keyword": Keyword,
    "Operator": Operator,
    "Punctuation": Punctuation,
    "String": String,
    "Number": Number,
    "Name.Function": Name.Function,
    "Name.Attribute": Name.Attribute,
    "Keyword.Type": Keyword.Type,
    "Generic.Subheading": Generic.Subheading,
    "Token.Error": Error,
}


def _hex_oklch(hex_str: str) -> tuple[float, float, float]:
    r, g, b = hex_to_rgb(hex_str)
    lab_l, a, b_lab = rgb_to_oklab(r, g, b)
    return oklab_to_oklch(lab_l, a, b_lab)


def _assert_matches_role(hex_val: str, role: str, *, label: str, tol: float = 15.0) -> None:
    _, chroma, hue = _hex_oklch(hex_val)
    if role in _NEUTRAL_ROLES:
        assert chroma < 0.02, f"{label}: expected near-neutral {role}, got chroma {chroma:.4f}"
        return
    anchor = ROLE_ANCHORS[role]
    diff = abs(hue - anchor.hue) % 360.0
    if diff > 180.0:
        diff = 360.0 - diff
    assert diff < tol, f"{label}: expected role {role} (hue {anchor.hue}), got hue {hue:.1f} ({hex_val})"
@pytest.mark.criteria("D-1")


def test_syntax_tokens_follow_monokai_pro_scope_convention() -> None:
    """Each Pygments token class carries the hue of the Monokai Pro scope it
    represents, not an arbitrary role. Regression coverage for
    Characterize point 2: today ``operator`` -> success (green, should be
    red), ``number`` -> warning (orange, should be purple), ``function`` ->
    info (cyan, should be green), ``comment`` -> pending (purple, should be
    near-neutral grey), and ``Text``/``Name`` both render as the cyan
    ``chrome`` hue instead of near-neutral foreground.
    """
    styles = SyntaxThemes.dark().styles
    for label, role in _TOKEN_ROLE.items():
        token = _TOKEN_BY_LABEL[label]
        hex_val = styles[token]
        assert isinstance(hex_val, str), (label, hex_val)
        _assert_matches_role(hex_val, role, label=label)


def test_generic_subheading_stays_populated_and_on_the_info_hue() -> None:
    """Generic.Subheading is absent from the visual-floor required-token list
    (tests/test_display_visual_floor.py::_REQUIRED_TOKENS), so a literal
    rewrite of the token table can silently drop its colour with a fully
    green gate. Pin it directly."""
    styles = SyntaxThemes.dark().styles
    hex_val = styles[Generic.Subheading]
    assert isinstance(hex_val, str)
    _assert_matches_role(hex_val, "info", label="Generic.Subheading")
