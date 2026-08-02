"""Fixed Pygments token palettes for Ralph's background-aware syntax previews."""

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


class SyntaxThemes:
    """Create the fixed palettes used by the display theme selector."""

    @staticmethod
    def dark() -> type[PygmentsStyle]:
        return _style(
            "#D0D0D0",
            ("#0CB9F2", "#C85BD0", "#6DDCF2", "#77D9B0", "#C9D921", "#94D90B"),
            ("#94D90B", "#0CB9F2", "#77D9B0"),
        )

    @staticmethod
    def light() -> type[PygmentsStyle]:
        return _style(
            "#202020",
            ("#854985", "#251947", "#36747A", "#3E4712", "#70703E", "#330B03"),
            ("#330B03", "#3E4712", "#36747A"),
        )

    @staticmethod
    def unknown() -> type[PygmentsStyle]:
        return _style(
            "#757575",
            ("#2070F0", "#2080A0", "#408070", "#5070D0", "#608020", "#7070A0"),
            ("#2070F0", "#408070", "#7070A0"),
        )


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
