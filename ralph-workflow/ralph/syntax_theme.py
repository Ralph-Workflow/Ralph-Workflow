"""Fixed Pygments token palettes for Ralph's background-aware syntax previews."""

from __future__ import annotations

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


class SyntaxThemes:
    """Create the fixed palettes used by the display theme selector."""

    @staticmethod
    def dark() -> type[PygmentsStyle]:
        return _style(
            "#D0D0D0",
            ("#0CB9F2", "#B543BF", "#6DDCF2", "#77D9B0", "#C9D921", "#94D90B"),
            ("#94D90B", "#0CB9F2", "#77D9B0"),
        )

    @staticmethod
    def light() -> type[PygmentsStyle]:
        return _style(
            "#202020",
            ("#854985", "#251947", "#3C7F85", "#3E4712", "#70703E", "#330B03"),
            ("#330B03", "#3E4712", "#3C7F85"),
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
        "styles": {
            Comment: comment,
            Keyword: keyword,
            Name: default,
            Name.Function: function,
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
    }
    return type("RalphSyntaxTheme", (PygmentsStyle,), namespace)
