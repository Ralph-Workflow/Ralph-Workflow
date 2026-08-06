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
