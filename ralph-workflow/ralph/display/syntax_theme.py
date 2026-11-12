from __future__ import annotations

from functools import lru_cache
from typing import Final, cast

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
    _CANONICAL_DARK_SURFACE_HEX,
    _CANONICAL_LIGHT_SURFACE_HEX,
    ROLE_ANCHORS,
    derive_preview_background,
    solve_dual_safe,
    solve_for_surface,
)


def _child_token(parent: object, name: str) -> object:
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
_TEXT_WHITESPACE = _child_token(Text, "Whitespace")
_LITERAL = _child_token(_child_token(pygments_token, "Token"), "Literal")
_ERROR = _child_token(_child_token(pygments_token, "Token"), "Error")
_GENERIC_HEADING = _child_token(Generic, "Heading")
_GENERIC_STRONG = _child_token(Generic, "Strong")
_GENERIC_PROMPT = _child_token(Generic, "Prompt")
_GENERIC_OUTPUT = _child_token(Generic, "Output")
_GENERIC_TRACEBACK = _child_token(Generic, "Traceback")
_GENERIC_ERROR = _child_token(Generic, "Error")
_GENERIC_EMPH = _child_token(Generic, "Emph")
_TOKEN_OTHER = _child_token(_child_token(pygments_token, "Token"), "Other")
_TOKEN_ESCAPE = _child_token(_child_token(pygments_token, "Token"), "Escape")

_SYNTAX_ROLES: Final[tuple[str, ...]] = (
    "foreground",
    "comment",
    "error",
    "skipped",
    "pending",
    "success",
    "warning",
    "info",
    "diff_removed",
    "diff_added",
)


def _generate_syntax_colors(preview_surface: str | None) -> dict[str, str]:
    if preview_surface is not None:
        return {
            role: solve_for_surface(ROLE_ANCHORS[role], preview_surface) for role in _SYNTAX_ROLES
        }
    return {role: solve_dual_safe(ROLE_ANCHORS[role]) for role in _SYNTAX_ROLES}


class SyntaxThemes:
    @staticmethod
    def dark() -> type[PygmentsStyle]:
        return _style(
            _generate_syntax_colors(derive_preview_background(_CANONICAL_DARK_SURFACE_HEX))
        )

    @staticmethod
    def light() -> type[PygmentsStyle]:
        return _style(
            _generate_syntax_colors(derive_preview_background(_CANONICAL_LIGHT_SURFACE_HEX))
        )

    @staticmethod
    def unknown() -> type[PygmentsStyle]:
        return _style(_generate_syntax_colors(None))

    @staticmethod
    def for_surface(surface_hex: str) -> type[PygmentsStyle]:
        return _syntax_theme_for_surface_cached(surface_hex)


def _syntax_theme_for_surface_uncached(surface_hex: str) -> type[PygmentsStyle]:
    preview_surface = derive_preview_background(surface_hex)
    return _style(_generate_syntax_colors(preview_surface))


_syntax_theme_for_surface_cached = lru_cache(maxsize=8)(_syntax_theme_for_surface_uncached)


def _style(colors: dict[str, str]) -> type[PygmentsStyle]:
    foreground = colors["foreground"]
    comment = colors["comment"]
    keyword = colors["error"]
    string = colors["skipped"]
    number = colors["pending"]
    function = colors["success"]
    attribute = colors["warning"]
    builtin = colors["info"]
    deleted = colors["diff_removed"]
    inserted = colors["diff_added"]
    subheading = colors["info"]
    namespace: dict[str, object] = {
        "default_style": foreground,
        "styles": cast(
            "object",
            {
                Comment: comment,
                _NAME_DECORATOR: comment,
                Keyword: keyword,
                _KEYWORD_NAMESPACE: keyword,
                Operator: keyword,
                Punctuation: keyword,
                _ERROR: keyword,
                Name: foreground,
                _NAME_VARIABLE: foreground,
                Text: foreground,
                _TEXT_WHITESPACE: foreground,
                String: string,
                Number: number,
                _LITERAL: number,
                _NAME_CONSTANT: number,
                Name.Function: function,
                _NAME_CLASS: function,
                _NAME_NAMESPACE: function,
                _NAME_ATTRIBUTE: attribute,
                _KEYWORD_TYPE: builtin,
                _NAME_BUILTIN: builtin,
                _NAME_BUILTIN_PSEUDO: builtin,
                Generic.Subheading: subheading,
                Generic.Deleted: deleted,
                Generic.Inserted: inserted,
                _GENERIC_HEADING: subheading,
                _GENERIC_STRONG: subheading,
                _GENERIC_PROMPT: foreground,
                _GENERIC_OUTPUT: foreground,
                _GENERIC_EMPH: foreground,
                _GENERIC_TRACEBACK: keyword,
                _GENERIC_ERROR: keyword,
                _TOKEN_OTHER: foreground,
                _TOKEN_ESCAPE: string,
            },
        ),
    }
    return type("RalphSyntaxTheme", (PygmentsStyle,), namespace)
