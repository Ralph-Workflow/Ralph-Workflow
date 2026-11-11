
from __future__ import annotations

from typing import Final

from rich.syntax import Syntax
from rich.text import Text

from ralph.display.line_sanitizer import strip_terminal_control
from ralph.display.theme import (
    diff_token_foregrounds,
    preview_background_for_background,
    syntax_theme_for_background,
)

_MAX_PREVIEW_LINES: Final[int] = 40

_MIN_RENDER_ROWS_FOR_BLOCK: Final[int] = 2

_ELISION_GLYPH: Final[str] = "…"

_ELISION_STYLE: Final[str] = "theme.display.elision"


def _diff_marker_style(
    removed: bool, *, terminal_bg_is_light: bool | None, surface_hex: str | None = None
) -> str:
    old_style, new_style = diff_token_foregrounds(terminal_bg_is_light, surface_hex=surface_hex)
    return old_style if removed else new_style


def _safe_lines(content: str, *, max_lines: int) -> tuple[list[str], list[str], int | None]:
    sanitized = strip_terminal_control(content)
    if not sanitized:
        return [], [], None
    raw_lines = sanitized.splitlines()
    if len(raw_lines) <= max_lines:
        return raw_lines, [], None
    head_count = max_lines // 2
    tail_count = max_lines - head_count
    return raw_lines[:head_count], raw_lines[-tail_count:], len(raw_lines) - max_lines


def _elision_line(omitted: int, glyph: str, overflow_ref: str | None, source: str) -> str:
    noun = "line" if omitted == 1 else "lines"
    citation = f" [see {overflow_ref}]" if overflow_ref else ""
    return f"{glyph} ({omitted} more {noun} · {len(source.encode('utf-8'))} B){citation}"


def _elision_text(
    omitted: int, glyph: str = _ELISION_GLYPH, overflow_ref: str | None = None, source: str = ""
) -> Text:
    return Text(_elision_line(omitted, glyph, overflow_ref, source), style=_ELISION_STYLE)


def _binary_note(content: str) -> Text | None:
    return Text("[binary content omitted]", style=_ELISION_STYLE) if "\x00" in content else None


def _make_syntax(
    body: str,
    lexer_name: str,
    *,
    is_markdown: bool,
    terminal_bg_is_light: bool | None,
    surface_hex: str | None = None,
    start_line: int = 1,
    background_color: str | None = None,
) -> Syntax:
    return Syntax(
        body,
        lexer_name,
        theme=syntax_theme_for_background(terminal_bg_is_light, surface_hex=surface_hex),
        line_numbers=True,
        word_wrap=is_markdown,
        background_color=background_color
        or preview_background_for_background(terminal_bg_is_light, surface_hex=surface_hex),
        start_line=start_line,
    )


def highlight_code_spans(
    code: str,
    language: str,
    *,
    terminal_bg_is_light: bool | None = None,
    surface_hex: str | None = None,
) -> list[tuple[int, int, str]]:
    if not code:
        return []
    from pygments.lexers import (
        get_lexer_by_name,
    )
    from pygments.util import ClassNotFound

    try:
        lexer = get_lexer_by_name(language)
    except ClassNotFound:
        return []
    except Exception:
        return []
    try:
        theme = syntax_theme_for_background(terminal_bg_is_light, surface_hex=surface_hex)
        spans: list[tuple[int, int, str]] = []
        offset = 0
        for token, text in lexer.get_tokens(code):
            end = offset + len(text)
            if text:
                style = theme.get_style_for_token(token)
                color = style.color
                if color is not None and color.is_default is False:
                    spans.append(
                        (
                            offset,
                            end,
                            f"bold {color.name}" if style.bold else color.name,
                        )
                    )
            offset = end
        return spans
    except Exception:
        return []
