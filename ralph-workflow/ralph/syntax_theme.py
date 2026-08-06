"""Monokai-derived Pygments token palettes for Ralph's background-aware syntax previews.

Each palette is solved per surface by :mod:`ralph.display._palette` rather
than read from a fixed table.
"""

from __future__ import annotations

from typing import cast

import pygments.token as pygments_token
from pygments.style import Style as PygmentsStyle
from pygments.token import (
    Comment,
    Generic,
    Keyword,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Text,
)

from ralph.display._palette import (
    ROLE_ANCHORS,
    derive_preview_background,
    solve_dual_safe,
    solve_for_surface,
)


def _child_token(parent: object, name: str) -> object:
    """Resolve a Pygments child token despite its incomplete type stubs."""
    return cast("object", getattr(parent, name))


_NAME_CLASS = _child_token(Name, "Class")
_NAME_NAMESPACE = _child_token(Name, "Namespace")
_NAME_BUILTIN = _child_token(Name, "Builtin")
_NAME_BUILTIN_PSEUDO = _child_token(_NAME_BUILTIN, "Pseudo")
_NAME_DECORATOR = _child_token(Name, "Decorator")
_NAME_ATTRIBUTE = _child_token(Name, "Attribute")
_NAME_VARIABLE = _child_token(Name, "Variable")
_NAME_CONSTANT = _child_token(Name, "Constant")
_KEYWORD_TYPE = _child_token(Keyword, "Type")
_KEYWORD_NAMESPACE = _child_token(Keyword, "Namespace")
_LITERAL = _child_token(_child_token(pygments_token, "Token"), "Literal")
_ERROR = _child_token(_child_token(pygments_token, "Token"), "Error")


def _generate_syntax_colors(
    preview_surface: str | None,
) -> tuple[str, tuple[str, str, str, str, str, str], tuple[str, str, str]]:
    if preview_surface is not None:
        default = solve_for_surface(ROLE_ANCHORS["chrome"], preview_surface)
        comment = solve_for_surface(ROLE_ANCHORS["pending"], preview_surface)
        keyword = solve_for_surface(ROLE_ANCHORS["error"], preview_surface)
        function = solve_for_surface(ROLE_ANCHORS["info"], preview_surface)
        string = solve_for_surface(ROLE_ANCHORS["skipped"], preview_surface)
        number = solve_for_surface(ROLE_ANCHORS["warning"], preview_surface)
        operator = solve_for_surface(ROLE_ANCHORS["success"], preview_surface)
        deleted = solve_for_surface(ROLE_ANCHORS["diff_removed"], preview_surface)
        inserted = solve_for_surface(ROLE_ANCHORS["diff_added"], preview_surface)
        subheading = solve_for_surface(ROLE_ANCHORS["info"], preview_surface)
    else:
        default = solve_dual_safe(ROLE_ANCHORS["chrome"])
        comment = solve_dual_safe(ROLE_ANCHORS["pending"])
        keyword = solve_dual_safe(ROLE_ANCHORS["error"])
        function = solve_dual_safe(ROLE_ANCHORS["info"])
        string = solve_dual_safe(ROLE_ANCHORS["skipped"])
        number = solve_dual_safe(ROLE_ANCHORS["warning"])
        operator = solve_dual_safe(ROLE_ANCHORS["success"])
        deleted = solve_dual_safe(ROLE_ANCHORS["diff_removed"])
        inserted = solve_dual_safe(ROLE_ANCHORS["diff_added"])
        subheading = solve_dual_safe(ROLE_ANCHORS["info"])

    colors = (comment, keyword, function, string, number, operator)
    diff_colors = (deleted, inserted, subheading)
    return default, colors, diff_colors


class SyntaxThemes:
    """Create the fixed palettes used by the display theme selector."""

    @staticmethod
    def dark() -> type[PygmentsStyle]:
        default, colors, diff_colors = _generate_syntax_colors("#101417")
        return _style(default, colors, diff_colors)

    @staticmethod
    def light() -> type[PygmentsStyle]:
        default, colors, diff_colors = _generate_syntax_colors("#F7F9FB")
        return _style(default, colors, diff_colors)

    @staticmethod
    def unknown() -> type[PygmentsStyle]:
        default, colors, diff_colors = _generate_syntax_colors(None)
        return _style(default, colors, diff_colors)

    @staticmethod
    def for_surface(surface_hex: str) -> type[PygmentsStyle]:
        preview_surface = derive_preview_background(surface_hex)
        default, colors, diff_colors = _generate_syntax_colors(preview_surface)
        return _style(default, colors, diff_colors)



def _style(
    default: str,
    colors: tuple[str, str, str, str, str, str],
    diff_colors: tuple[str, str, str],
) -> type[PygmentsStyle]:
    comment, keyword, function, string, number, operator = colors
    deleted, inserted, subheading = diff_colors
    namespace: dict[str, object] = {
        "default_style": default,
        "styles": cast(
            "object",
            {
            Comment: comment,
            Keyword: keyword,
            Name: default,
            Name.Function: function,
            _NAME_CLASS: function,
            _NAME_NAMESPACE: function,
            _NAME_BUILTIN: keyword,
            _NAME_BUILTIN_PSEUDO: keyword,
            _NAME_DECORATOR: comment,
            _NAME_ATTRIBUTE: function,
            _NAME_VARIABLE: function,
            _NAME_CONSTANT: number,
            _KEYWORD_TYPE: keyword,
            _KEYWORD_NAMESPACE: keyword,
            _LITERAL: number,
            _ERROR: comment,
            String: string,
            Number: number,
            Operator: operator,
            Text: default,
            Punctuation: operator,
            Text.Whitespace: default,
            Generic.Subheading: subheading,
            Generic.Deleted: deleted,
            Generic.Inserted: inserted,
            },
        ),
    }
    return type("RalphSyntaxTheme", (PygmentsStyle,), namespace)
