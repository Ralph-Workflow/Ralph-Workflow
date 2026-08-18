"""Low-level, background-aware rendering primitives shared by edit-preview builders.

Split out of :mod:`ralph.display.edit_preview` so that module's line count
stays under the repo-structure file-size floor. This module owns the bounded
preview-length contract (``_MAX_PREVIEW_LINES``), the muted elision-line
builders, the binary-content guard, the diff-marker foreground resolver, and
the themed ``Syntax`` renderable builder -- the pieces every preview shape
(write, multi-read, search-result, edit-diff, content) draws on. It is pure:
no Console, no I/O, no env reads.
"""

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

#: Maximum number of lines a preview may show. Anything longer is
#: truncated and replaced with a muted elision line so the preview
#: stays scannable and the panel cannot grow without bound.
_MAX_PREVIEW_LINES: Final[int] = 40

#: A diff block needs one marker row and at least one source row.
_MIN_RENDER_ROWS_FOR_BLOCK: Final[int] = 2

#: Elision line shown when the content exceeds ``_MAX_PREVIEW_LINES``.
#: Uses the Unicode horizontal-ellipsis glyph so the operator sees a
#: clear truncation cue.
_ELISION_GLYPH: Final[str] = "…"

#: Muted style used for the ``… (N more lines)`` elision line. It is an
#: explicit semantic role, rather than a default-foreground fallback.
_ELISION_STYLE: Final[str] = "theme.display.elision"


def _diff_marker_style(
    removed: bool, *, terminal_bg_is_light: bool | None, surface_hex: str | None = None
) -> str:
    """Return the distinct resolved diff-polarity marker foreground."""
    old_style, new_style = diff_token_foregrounds(terminal_bg_is_light, surface_hex=surface_hex)
    return old_style if removed else new_style


def _safe_lines(content: str, *, max_lines: int) -> tuple[list[str], list[str], int | None]:
    """Sanitize and middle-trim content into head, tail, and omitted count."""
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
    """Return the shared count, size, and destination elision contract."""
    noun = "line" if omitted == 1 else "lines"
    citation = f" [see {overflow_ref}]" if overflow_ref else ""
    return f"{glyph} ({omitted} more {noun} · {len(source.encode('utf-8'))} B){citation}"


def _elision_text(
    omitted: int, glyph: str = _ELISION_GLYPH, overflow_ref: str | None = None, source: str = ""
) -> Text:
    """Build a muted elision Text for the truncation marker."""
    return Text(_elision_line(omitted, glyph, overflow_ref, source), style=_ELISION_STYLE)


def _binary_note(content: str) -> Text | None:
    """Return a safe note instead of rendering NUL-containing binary content."""
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
    """Build the themed ``Syntax`` renderable used by both preview shapes.

    Known backgrounds are an owned, complete preview surface while the theme
    uses fixed RGB token colours. Unknown backgrounds intentionally use the
    dual-safe transparent fallback rather than terminal-defined ANSI slots.
    """
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
    """Return lexer-derived ``(start, end, style)`` spans for ``code``.

    Shared transport-neutral seam: the same themed Pygments highlighting
    that powers the edit-preview ``Syntax`` renderable, exposed as plain
    character-offset spans so inline text renderers (e.g. the canonical
    agent-event registry rendering a fenced AGY text event) can stylize a
    code region inside a larger :class:`rich.text.Text` without building a
    block-level panel.

    Fail-closed: any Pygments / theme failure, an empty ``code``, or an
    unknown ``language`` returns an empty list so the caller falls back to
    its plain body style rather than surfacing a renderer exception.
    """
    if not code:
        return []
    from pygments.lexers import (
        get_lexer_by_name,  # reason: deferred import keeps the display hot path cheap
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
