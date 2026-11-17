from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

from functools import lru_cache
from typing import Final, Protocol, TypedDict, cast, runtime_checkable

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
    _TokenType,
)

from ralph.display._palette import (
    _CANONICAL_DARK_SURFACE_HEX,
    _CANONICAL_LIGHT_SURFACE_HEX,
    ROLE_ANCHORS,
    derive_preview_background,
    solve_dual_safe,
    solve_for_surface,
)


@runtime_checkable
class _BuiltinNameToken(Protocol):
    Pseudo: object


@runtime_checkable
class _NameToken(Protocol):
    Class: object
    Namespace: object
    Builtin: _BuiltinNameToken
    Decorator: object
    Attribute: object
    Variable: object
    Constant: object


@runtime_checkable
class _KeywordToken(Protocol):
    Type: object
    Namespace: object


@runtime_checkable
class _GenericExtras(Protocol):
    Heading: object
    Strong: object
    Prompt: object
    Output: object
    Traceback: object
    Error: object
    Emph: object


@runtime_checkable
class _RootToken(Protocol):
    Literal: object
    Error: object
    Other: object
    Escape: object


@runtime_checkable
class _PygmentsTokenModule(Protocol):
    Token: _RootToken


class _StyleNamespace(TypedDict):
    default_style: str
    styles: dict[object, str]


def _require_name_token(value: object) -> _NameToken:
    if not isinstance(value, _NameToken):
        raise TypeError("pygments Name token shape mismatch")
    return value


def _require_keyword_token(value: object) -> _KeywordToken:
    if not isinstance(value, _KeywordToken):
        raise TypeError("pygments Keyword token shape mismatch")
    return value


def _require_generic_extras(value: object) -> _GenericExtras:
    if not isinstance(value, _GenericExtras):
        raise TypeError("pygments Generic token shape mismatch")
    return value


def _require_token_module(value: object) -> _PygmentsTokenModule:
    if not isinstance(value, _PygmentsTokenModule):
        raise TypeError("pygments.token module shape mismatch")
    return value


_NAME = _require_name_token(Name)
_KEYWORD = _require_keyword_token(Keyword)
_GENERIC = _require_generic_extras(Generic)
_TOKEN_ROOT = _require_token_module(pygments_token).Token

_NAME_CLASS = _NAME.Class
_NAME_NAMESPACE = _NAME.Namespace
_NAME_BUILTIN = _NAME.Builtin
_NAME_BUILTIN_PSEUDO = _NAME_BUILTIN.Pseudo
_NAME_DECORATOR = _NAME.Decorator
_NAME_ATTRIBUTE = _NAME.Attribute
_NAME_VARIABLE = _NAME.Variable
_NAME_CONSTANT = _NAME.Constant
_KEYWORD_TYPE = _KEYWORD.Type
_KEYWORD_NAMESPACE = _KEYWORD.Namespace
_TEXT_WHITESPACE = Text.Whitespace
_LITERAL = _TOKEN_ROOT.Literal
_ERROR = _TOKEN_ROOT.Error
_GENERIC_HEADING = _GENERIC.Heading
_GENERIC_STRONG = _GENERIC.Strong
_GENERIC_PROMPT = _GENERIC.Prompt
_GENERIC_OUTPUT = _GENERIC.Output
_GENERIC_TRACEBACK = _GENERIC.Traceback
_GENERIC_ERROR = _GENERIC.Error
_GENERIC_EMPH = _GENERIC.Emph
_TOKEN_OTHER = _TOKEN_ROOT.Other
_TOKEN_ESCAPE = _TOKEN_ROOT.Escape

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
    style_map: dict[object, str] = {
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
    }
    namespace: _StyleNamespace = {
        "default_style": foreground,
        "styles": style_map,
    }

    class RalphSyntaxTheme(PygmentsStyle):
        default_style = namespace["default_style"]
        styles = cast("Mapping[_TokenType, str]", namespace["styles"])

    return RalphSyntaxTheme
