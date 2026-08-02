"""Build syntax-highlighted previews of agent file-edit tool calls.

When ``ParallelDisplay`` emits a TOOL_USE event for one of the
content-edit tools (``write_file``, ``append_file``, ``edit_file``,
``ralph_edit_md_artifact``, ``ralph_stage_md_artifact``,
``ralph_submit_md_artifact``), the operator already sees the existing
one-line registry header line carrying the friendly tool name and the
formatted argument repr. That line stays byte-identical (the consolidation
contract). What this module contributes is purely additive: a
syntax-highlighted preview block printed *after* the header so the
operator can read the actual edit content in the live console instead of
having to open the file or scroll back through the overflow log.

Design:

* The builder is pure: it takes ``(tool_name, input_dict, width)`` and
  returns a ``rich`` renderable (or ``None`` for non-content tools /
  empty payloads). No Console, no I/O, no env reads. The display owner
  routes the renderable through its own Console, quiet-gates the print,
  and suppresses exceptions so a malformed renderable cannot break the
  live display path (R-3).

* Language inference is delegated to
  :func:`ralph.display.language_inference.lexer_for_path`; inference
  failures degrade to ``"text"`` (plain text) rather than raising.
  Artifact tools that carry no ``path`` key (``ralph_edit_md_artifact``,
  ``ralph_stage_md_artifact``, ``ralph_submit_md_artifact``) default
  to ``"markdown"`` because their content is always a markdown
  artifact document.

* All agent-derived content is sanitized through
  :func:`ralph.display.line_sanitizer.strip_terminal_control` before
  it enters any renderable, so a hostile escape sequence can never
  paint the operator's terminal (R-1). The
  ``audit_terminal_escape_containment`` audit pins this contract and
  the dedicated ``tests/display/test_terminal_escape_containment.py``
  suite contains the black-box assertions.

* Markdown content gets word-wrap (so headings and paragraphs don't
  blow past the panel width); non-markdown content does NOT wrap
  (wrapping python code would garble indentation).

* Write-style tools (``write_file`` / ``append_file`` /
  ``ralph_stage_md_artifact`` / ``ralph_submit_md_artifact``): the
  content is wrapped in a ``rich.syntax.Syntax`` with
  ``line_numbers=True``; markdown uses :func:`render_markdown_preview` instead.

* Edit-style tools (``edit_file`` / ``ralph_edit_md_artifact``):
  every entry in ``edits`` is rendered as a ``- old / + new`` diff
  with both hunks syntax-highlighted under ``line_numbers=True`` using the same lexer.
  A positive integer ``start_line`` on an edit starts its new-content
  line numbers at that known file position; absent or invalid values
  remain snippet-relative at 1. When the caller resolves a known terminal
  background, each removed/added hunk owns its complete derived fill,
  including its gutter; undetermined backgrounds remain transparent.
  Literal ``-`` and ``+`` markers retain the ``theme.status.error`` /
  ``theme.status.success`` carriers, so the diff remains readable when
  color is disabled.

* A bounded preview cap (``_MAX_PREVIEW_LINES = 40``) trims long
  content and appends a muted ``\u2026 (N more lines)`` elision line
  so the operator knows the file is bigger than what is shown.

* Zero hex color literals: the new module references named
  ``STATUS_STYLES`` style keys (``"success"``, ``"error"``,
  ``"theme.text.muted"``) instead of literal hex values, so the
  wider ``tests/display/test_no_hex_colors_outside_theme.py``
  anti-drift guard (which walks every ``ralph/display/*.py``) does
  not flag this module (R-2).

Background-aware highlighting
-----------------------------

Highlight colours are supplied by the fixed-RGB, contrast-tested Pygments
themes in :func:`ralph.display.theme.syntax_theme_for_background`. Known
backgrounds use one owned surface across the complete preview; an unknown
background deliberately falls back to transparent, dual-safe rendering.

The previous implementation pinned pygments' ``default`` theme, which
is a *light-background* theme: it renders plain identifiers and
punctuation at pure black and keywords at dark navy. Combined with a
transparent background that made most of a preview invisible on the
(overwhelmingly common) dark terminal. The background is resolved once
by the display owner via
:func:`ralph.display.theme.detect_terminal_background_is_light` -- which
asks the terminal for its actual background colour rather than assuming
one -- and threaded in as ``terminal_bg_is_light``; this module stays
pure and reads no env.

The diff ``-`` / ``+`` markers are resolved the same way, through
:func:`ralph.display.theme.pick_status_styles`, so the marker colours
also clear WCAG contrast on a light terminal. Known-background diff rows
receive the matched complete fill supplied by
:func:`ralph.display.theme.diff_fill_styles`; the unknown-background path
keeps the transparent dual-safe fallback.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, ClassVar, Final, cast

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.markdown import CodeBlock, Markdown, MarkdownElement
from rich.padding import Padding
from rich.style import Style
from rich.syntax import Syntax
from rich.text import Text

from ralph._markdown_theme import markdown_theme_context
from ralph.display.language_inference import lexer_for_path
from ralph.display.line_sanitizer import strip_terminal_control
from ralph.display.preview_payload import PreviewPayload, payload_from_tool_event
from ralph.display.theme import (
    pick_status_styles,
    preview_background_for_background,
    syntax_theme_for_background,
)

if TYPE_CHECKING:
    from rich.console import RenderableType

#: MCP and alias prefixes stripped from a tool name. Order matters: the
#: longer ``mcp__ralph__`` prefix is checked first.
_MCP_RALPH_PREFIX: Final[str] = "mcp__ralph__"
_RA_PREFIX: Final[str] = "ralph."

#: Maximum number of lines a preview may show. Anything longer is
#: truncated and replaced with a muted elision line so the preview
#: stays scannable and the panel cannot grow without bound.
_MAX_PREVIEW_LINES: Final[int] = 40

#: A diff block needs one marker row and at least one source row.
_MIN_RENDER_ROWS_FOR_BLOCK: Final[int] = 2

#: Elision line shown when the content exceeds ``_MAX_PREVIEW_LINES``.
#: Uses the Unicode horizontal-ellipsis glyph so the operator sees a
#: clear truncation cue.
_ELISION_GLYPH: Final[str] = "\u2026"

#: Semantic status names used by the diff-style old / new line markers.
#: They are resolved through
#: :func:`ralph.display.theme.pick_status_styles` so the marker colour
#: is the background-appropriate variant (``error`` -> vermillion,
#: ``success`` -> bluish-green, darkened on a light terminal). No hex
#: literal appears here.
_DIFF_OLD_STATUS: Final[str] = "error"
_DIFF_NEW_STATUS: Final[str] = "success"

#: Muted style used for the ``… (N more lines)`` elision line. ``dim``
#: is a terminal attribute rather than a colour, so it reads correctly
#: on either background.
_ELISION_STYLE: Final[str] = "theme.text.muted"


def _diff_marker_style(status: str, *, terminal_bg_is_light: bool | None) -> str:
    """Return the Rich style string for a diff marker on this background."""
    return pick_status_styles(terminal_bg_is_light)[status][0]


def _normalize_tool_name(name: str) -> str:
    """Strip the MCP / alias prefix and return the bare tool name."""
    if name.startswith(_MCP_RALPH_PREFIX):
        return name[len(_MCP_RALPH_PREFIX) :]
    if name.startswith(_RA_PREFIX):
        return name[len(_RA_PREFIX) :]
    return name


#: Lexer aliases that identify markdown. ``get_lexer_for_filename``
#: reports ``"markdown"`` first, but ``"md"`` is the alias some
#: pygments versions lead with, so both are accepted.
_MARKDOWN_LEXER_ALIASES: Final[frozenset[str]] = frozenset({"markdown", "md"})


def _is_markdown_lexer(lexer_name: str) -> bool:
    """Return True when ``lexer_name`` should trigger word-wrap.

    Markdown is the only lexer that benefits from word-wrap; code
    must NOT wrap or indentation breaks.
    """
    return lexer_name.lower() in _MARKDOWN_LEXER_ALIASES


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


def preview_header(tool_name: str, path: str | None, *, glyphs_enabled: bool = True) -> Text:
    """Return the structural file-content header shown before a preview body."""
    marker = "▸" if glyphs_enabled else ">"
    bare = _normalize_tool_name(tool_name)
    target = f"  {strip_terminal_control(path)}" if path else ""
    return Text(f"  {marker} {bare.removesuffix('_file')}{target}", style="theme.text.emphasis")


def preview_record_text(
    tool_name: str,
    input_dict: dict[str, object] | PreviewPayload,
    *,
    overflow_ref: str | None = None,
    glyphs_enabled: bool = True,
) -> tuple[str, str | None]:
    """Return a plain preview projection and its full sanitized source.

    The display owner writes the latter only when the bounded live preview
    elides content; keeping file I/O outside this pure module preserves the
    preview builder's no-I/O contract.
    """
    canonical = (
        input_dict
        if isinstance(input_dict, PreviewPayload)
        else payload_from_tool_event(
            tool_name,
            input_dict
            if any(key in input_dict for key in ("input", "args", "arguments"))
            else {"input": input_dict},
        )
    )
    if canonical is None:
        return "", None
    marker = ">" if not glyphs_enabled else "▸"
    bare = _normalize_tool_name(tool_name)
    target = f"  {strip_terminal_control(canonical.path)}" if canonical.path else ""
    lines = [f"  {marker} {bare.removesuffix('_file')}{target}"]
    source_parts: list[str] = []
    for hunk in canonical.hunks:
        if hunk.label:
            lines.append(f"  {strip_terminal_control(hunk.label)}")
        for prefix, body in (("-", hunk.old_text), ("+", hunk.new_text)):
            safe = strip_terminal_control(body)
            if not safe:
                continue
            source_parts.append(safe)
            start = hunk.start_line or 1
            for number, line in enumerate(safe.splitlines(), start):
                lines.append(f"{prefix} {number:>4} {line}")
    content = canonical.content
    if isinstance(content, str) and _normalize_tool_name(tool_name) in {
        "grep_files",
        "search_files",
    }:
        search_lines = _search_record_lines(content)
        if search_lines is not None:
            source_parts.extend(search_lines)
            lines.extend(search_lines[:_MAX_PREVIEW_LINES])
            omitted = len(search_lines) - _MAX_PREVIEW_LINES
            if omitted > 0:
                lines.append(
                    _elision_line(
                        omitted,
                        "..." if not glyphs_enabled else _ELISION_GLYPH,
                        overflow_ref,
                        "\n".join(search_lines),
                    )
                )
    elif isinstance(content, str) and content:
        safe = strip_terminal_control(content)
        source_parts.append(safe)
        start = canonical.start_line or 1
        for number, line in enumerate(safe.splitlines(), start):
            lines.append(f"  {number:>4} {line}")
    full_source = "\n".join(source_parts) or None
    visible_source_lines = sum(part.count("\n") + 1 for part in source_parts)
    if visible_source_lines > _MAX_PREVIEW_LINES and _normalize_tool_name(tool_name) not in {
        "grep_files",
        "search_files",
    }:
        omitted = visible_source_lines - _MAX_PREVIEW_LINES
        lines = lines[: _MAX_PREVIEW_LINES + 1]
        lines.append(
            _elision_line(
                omitted,
                "..." if not glyphs_enabled else _ELISION_GLYPH,
                overflow_ref,
                full_source or "",
            )
        )
    return "\n".join(lines), full_source


class _BackgroundAwareCodeBlock(CodeBlock):
    """Rich fenced-code block using Ralph's fixed, transparent palette."""

    @classmethod
    def create(cls, markdown: Markdown, token: object) -> _BackgroundAwareCodeBlock:
        node_info = str(cast("object", getattr(token, "info", "")) or "")
        candidate = cast("object", getattr(markdown, "terminal_bg_is_light", None))
        background = candidate if isinstance(candidate, bool) else None
        return cls(node_info.partition(" ")[0] or "text", background)

    def __init__(self, lexer_name: str, terminal_bg_is_light: bool | None) -> None:
        self.lexer_name = lexer_name
        self.terminal_bg_is_light = terminal_bg_is_light

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console, options
        yield Syntax(
            str(self.text).rstrip(),
            self.lexer_name,
            theme=syntax_theme_for_background(self.terminal_bg_is_light),
            background_color=preview_background_for_background(self.terminal_bg_is_light),
            padding=1,
        )


class _BackgroundAwareMarkdown(Markdown):
    """Markdown whose fenced code shares preview syntax contrast guarantees."""

    elements: ClassVar[dict[str, type[MarkdownElement]]] = {
        **Markdown.elements,
        "fence": _BackgroundAwareCodeBlock,
        "code_block": _BackgroundAwareCodeBlock,
    }

    def __init__(self, markup: str, *, terminal_bg_is_light: bool | None) -> None:
        super().__init__(markup)
        self.terminal_bg_is_light = terminal_bg_is_light

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        with markdown_theme_context(console, terminal_bg_is_light=self.terminal_bg_is_light):
            content = tuple(super().__rich_console__(console, options))
        surface = preview_background_for_background(self.terminal_bg_is_light)
        if surface == "default":
            yield from content
            return
        # An expanded style wrapper paints every Markdown row (including prose,
        # padding, and fenced code) without introducing body-frame chrome.
        yield Padding(
            _MarkdownContent(content),
            pad=(0, 0),
            style=Style(bgcolor=surface),
            expand=True,
        )


class _MarkdownContent:
    """Wrap Rich's Markdown render stream without asserting foreign item types."""

    def __init__(self, content: RenderResult) -> None:
        self._content = content

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console, options
        yield from self._content


def render_markdown_preview(
    text: str,
    *,
    width: int,
    terminal_bg_is_light: bool | None,
) -> Markdown:
    """Return the shared, sanitized Markdown renderer used by display previews."""
    del width
    return _BackgroundAwareMarkdown(
        strip_terminal_control(text), terminal_bg_is_light=terminal_bg_is_light
    )


def _make_syntax(
    body: str,
    lexer_name: str,
    *,
    is_markdown: bool,
    terminal_bg_is_light: bool | None,
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
        theme=syntax_theme_for_background(terminal_bg_is_light),
        line_numbers=True,
        word_wrap=is_markdown,
        background_color=background_color
        or preview_background_for_background(terminal_bg_is_light),
        start_line=start_line,
    )


def _build_write_preview(
    tool_name: str,
    path: str | None,
    content: str,
    *,
    width: int,
    terminal_bg_is_light: bool | None,
    start_line: int = 1,
    overflow_ref: str | None = None,
    glyphs_enabled: bool = True,
    max_lines: int = _MAX_PREVIEW_LINES,
) -> RenderableType | None:
    """Build a ``Syntax``-based preview for ``write_file`` / ``append_file``
    and the two artifact-stage / artifact-submit tools."""
    if not content:
        return None
    lexer_name = "markdown" if path is None else lexer_for_path(path, content)
    is_markdown = _is_markdown_lexer(lexer_name)
    binary = _binary_note(content)
    if binary is not None:
        return binary
    head, tail, omitted = _safe_lines(content, max_lines=max_lines)
    if not head:
        return None

    def render(lines: list[str], line: int) -> RenderableType:
        body = "\n".join(lines)
        return (
            render_markdown_preview(body, width=width, terminal_bg_is_light=terminal_bg_is_light)
            if is_markdown
            else _make_syntax(
                body,
                lexer_name,
                is_markdown=False,
                terminal_bg_is_light=terminal_bg_is_light,
                start_line=line,
            )
        )

    preview = render(head, start_line)
    if omitted is None:
        return preview
    return Group(
        preview,
        _elision_text(
            omitted, "..." if not glyphs_enabled else _ELISION_GLYPH, overflow_ref, content
        ),
        render(tail, start_line + len(head) + omitted),
    )


def _build_multiple_read_preview(
    content: str,
    *,
    width: int,
    terminal_bg_is_light: bool | None,
    glyphs_enabled: bool,
    overflow_ref: str | None,
) -> RenderableType | None:
    """Render each successful ``read_multiple_files`` result as its own file block."""
    try:
        envelope: object = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(envelope, dict):
        return None
    files_obj: object = envelope.get("files")
    if not isinstance(files_obj, list):
        return None
    blocks: list[RenderableType] = []
    files: list[object] = list(files_obj)
    remaining = _MAX_PREVIEW_LINES
    omitted_total = 0
    for entry_obj in files:
        if not isinstance(entry_obj, dict):
            continue
        path: object = entry_obj.get("path")
        body: object = entry_obj.get("content")
        line_start: object = entry_obj.get("line_start")
        if not isinstance(path, str) or not isinstance(body, str) or not body:
            continue
        line_count = len(strip_terminal_control(body).splitlines())
        shown = min(line_count, remaining)
        omitted_total += line_count - shown
        if shown <= 0:
            continue
        preview = _build_write_preview(
            "read_multiple_files",
            path,
            body,
            width=width,
            terminal_bg_is_light=terminal_bg_is_light,
            start_line=line_start if isinstance(line_start, int) and line_start > 0 else 1,
            glyphs_enabled=glyphs_enabled,
            max_lines=shown,
        )
        remaining -= shown
        if preview is not None:
            blocks.extend(
                (Text(f"  {strip_terminal_control(path)}", style="theme.text.muted"), preview)
            )
    if omitted_total:
        blocks.append(
            _elision_text(
                omitted_total,
                "..." if not glyphs_enabled else _ELISION_GLYPH,
                overflow_ref,
                content,
            )
        )
    return Group(*blocks) if blocks else None


def _search_record_lines(content: str) -> list[str] | None:
    """Return greppable numbered hit lines from a result envelope, if present."""
    try:
        envelope: object = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(envelope, dict) or not isinstance(envelope.get("matches"), list):
        return None
    lines: list[str] = []
    matches = envelope.get("matches")
    if not isinstance(matches, list):
        return None
    for match in matches:
        if isinstance(match, str):
            lines.append(f"  {strip_terminal_control(match)}")
        elif isinstance(match, dict):
            path, line, text = match.get("path"), match.get("line"), match.get("text")
            if isinstance(path, str) and isinstance(line, int) and isinstance(text, str):
                lines.append(
                    f"  {strip_terminal_control(path)}:{line:>4} {strip_terminal_control(text)}"
                )
    return lines


def _build_search_result_preview(
    content: str,
    *,
    pattern: str | None,
    terminal_bg_is_light: bool | None,
    glyphs_enabled: bool,
    overflow_ref: str | None,
) -> RenderableType | None:
    """Render structured grep/search matches with per-file lexers and real gutters."""
    try:
        envelope: object = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(envelope, dict):
        return None
    matches: object = envelope.get("matches")
    if not isinstance(matches, list):
        return None
    blocks: list[RenderableType] = []
    omitted = 0
    for match in matches:
        if len(blocks) >= _MAX_PREVIEW_LINES:
            omitted += 1
            continue
        if isinstance(match, str):
            blocks.append(Text(f"  {strip_terminal_control(match)}", style="theme.text.muted"))
            continue
        if not isinstance(match, dict):
            continue
        path, line, text = match.get("path"), match.get("line"), match.get("text")
        if not isinstance(path, str) or not isinstance(line, int) or not isinstance(text, str):
            continue
        safe = strip_terminal_control(text)
        binary = _binary_note(safe)
        if binary is not None:
            blocks.extend(
                (Text(f"  {strip_terminal_control(path)}", style="theme.text.muted"), binary)
            )
            continue
        syntax = _make_syntax(
            safe,
            lexer_for_path(strip_terminal_control(path), safe),
            is_markdown=False,
            terminal_bg_is_light=terminal_bg_is_light,
            start_line=line,
        )
        if pattern and (start := safe.find(pattern)) >= 0:
            syntax.stylize_range(
                f"bold {_diff_marker_style(_DIFF_NEW_STATUS, terminal_bg_is_light=terminal_bg_is_light)}",
                (1, start),
                (1, start + len(pattern)),
                style_before=False,
            )
        blocks.extend((Text(f"  {strip_terminal_control(path)}", style="theme.text.muted"), syntax))
    if omitted:
        blocks.append(
            _elision_text(
                omitted, "..." if not glyphs_enabled else _ELISION_GLYPH, overflow_ref, content
            )
        )
    return Group(*blocks) if blocks else None


def _build_edit_preview(
    path: str | None,
    edits: list[dict[str, object]],
    *,
    width: int,
    terminal_bg_is_light: bool | None,
    diff_fills: tuple[str, str] | None = None,
    overflow_ref: str | None = None,
    glyphs_enabled: bool = True,
) -> RenderableType | None:
    """Build a diff-style preview for ``edit_file`` / ``ralph_edit_md_artifact``.

    ``diff_fills`` paints complete removed/added known-background rows,
    including their numbered gutters; ``None`` keeps the unknown-background
    fallback transparent. Both the old (``-``) and new (``+``) hunks are syntax-highlighted
    through :func:`_make_syntax` with the same path-derived lexer. Only
    the literal marker glyphs use the ``theme.status.error`` /
    ``theme.status.success`` styles, preserving the diff when colour is
    stripped. A positive ``start_line`` uses the real file gutter;
    otherwise a visible ``(snippet)`` marker identifies relative lines.

    Edit tools without a ``path`` (``ralph_edit_md_artifact``) default
    the lexer to ``"markdown"`` because the artifact content is
    always a markdown document.
    """
    del width  # reserved for future width-aware wrapping; currently unused
    if not edits:
        return None
    lexer_name = "markdown" if path is None else lexer_for_path(path or "")
    is_markdown = _is_markdown_lexer(lexer_name)
    old_style = _diff_marker_style(_DIFF_OLD_STATUS, terminal_bg_is_light=terminal_bg_is_light)
    new_style = _diff_marker_style(_DIFF_NEW_STATUS, terminal_bg_is_light=terminal_bg_is_light)
    old_fill, new_fill = diff_fills or (None, None)
    safe_edits = [
        (
            edit,
            strip_terminal_control(str(edit.get("oldText", "") or "")),
            strip_terminal_control(str(edit.get("newText", "") or "")),
        )
        for edit in edits
    ]
    source_blocks = sum(bool(old) + bool(new) for _, old, new in safe_edits)
    full_source = "\n".join(part for _, old, new in safe_edits for part in (old, new) if part)
    if not source_blocks:
        return None
    blocks: list[RenderableType] = []
    # Reserve one row for the shared elision marker when this preview overflows.
    remaining_lines = _MAX_PREVIEW_LINES - 1
    remaining_blocks = source_blocks
    total_omitted = 0
    for edit, old_safe, new_safe in safe_edits:
        if not old_safe and not new_safe:
            continue
        label = edit.get("label")
        if isinstance(label, str) and label and remaining_lines > 0:
            blocks.append(Text(f"  {strip_terminal_control(label)}", style="theme.text.muted"))
            remaining_lines -= 1
        start_line_obj = edit.get("start_line")
        start_line = (
            start_line_obj
            if isinstance(start_line_obj, int)
            and not isinstance(start_line_obj, bool)
            and start_line_obj > 0
            else 1
        )
        if start_line_obj != start_line and remaining_lines > 0:
            blocks.append(Text("  (snippet)", style="theme.text.muted"))
            remaining_lines -= 1
        for marker, source, style, fill in (
            ("-", old_safe, old_style, old_fill),
            ("+", new_safe, new_style, new_fill),
        ):
            if not source:
                continue
            source_lines = len(source.splitlines())
            if remaining_lines < _MIN_RENDER_ROWS_FOR_BLOCK:
                total_omitted += source_lines
                remaining_blocks -= 1
                continue
            allocation = min(remaining_lines - 1, max(1, remaining_lines // remaining_blocks))
            head, tail, omitted = _safe_lines(source, max_lines=allocation)
            shown = len(head) + len(tail)
            remaining_lines -= shown + 1  # marker plus syntax rows
            remaining_blocks -= 1
            total_omitted += omitted or 0
            blocks.append(
                Group(
                    Text(marker, style=f"{style} on {fill}" if fill else style),
                    _make_syntax(
                        "\n".join(head),
                        lexer_name,
                        is_markdown=is_markdown,
                        terminal_bg_is_light=terminal_bg_is_light,
                        start_line=start_line,
                        background_color=fill,
                    ),
                )
            )
            if tail:
                tail_start = start_line + len(head) + (omitted or 0)
                blocks.append(
                    _make_syntax(
                        "\n".join(tail),
                        lexer_name,
                        is_markdown=is_markdown,
                        terminal_bg_is_light=terminal_bg_is_light,
                        start_line=tail_start,
                        background_color=fill,
                    )
                )
    if total_omitted:
        blocks.append(
            _elision_text(
                total_omitted,
                "..." if not glyphs_enabled else _ELISION_GLYPH,
                overflow_ref,
                full_source,
            )
        )
    return Group(*blocks)


def _build_content_preview(
    bare: str,
    canonical: PreviewPayload,
    content: str,
    path: str | None,
    start_line: int,
    *,
    width: int,
    terminal_bg_is_light: bool | None,
    diff_fills: tuple[str, str] | None,
    overflow_ref: str | None,
    glyphs_enabled: bool,
) -> RenderableType | None:
    """Render a content payload, keeping diff metadata outside file gutters."""
    preview_path = path
    is_diff = (
        bare in {"git_diff", "git_show"}
        or canonical.language_hint == "diff"
        or (canonical.is_snippet and bare != "read_file")
    )
    is_relative_snippet = canonical.is_snippet
    if is_diff:
        preview_path = "preview.diff"
    elif canonical.language_hint:
        preview_path = f"preview.{canonical.language_hint}"
    elif bare == "git_log":
        preview_path = "preview.txt"
    hunks = list(re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", content, re.MULTILINE))
    if is_diff and hunks:
        blocks: list[RenderableType] = []
        old_fill, new_fill = diff_fills or (None, None)

        def render_diff_rows(lines: list[str], start: int) -> RenderableType:
            """Render each changed diff row on its complete resolved polarity fill."""
            lexer = lexer_for_path(preview_path, "\n".join(lines))
            return Group(
                *(
                    _make_syntax(
                        row,
                        lexer,
                        is_markdown=False,
                        terminal_bg_is_light=terminal_bg_is_light,
                        start_line=line_number,
                        background_color=(
                            old_fill
                            if row.startswith("-")
                            else new_fill
                            if row.startswith("+")
                            else None
                        ),
                    )
                    for line_number, row in enumerate(lines, start)
                )
            )

        remaining = _MAX_PREVIEW_LINES - 1
        omitted_total = 0
        omitted_source: list[str] = []
        for index, hunk in enumerate(hunks):
            body_end = hunks[index + 1].start() if index + 1 < len(hunks) else len(content)
            body = strip_terminal_control(content[hunk.end() : body_end].strip("\n"))
            body_lines = body.splitlines()
            if remaining <= 1:
                omitted_total += len(body_lines)
                omitted_source.extend(body_lines)
                continue
            header_start = 0 if index == 0 else hunk.start()
            header = strip_terminal_control(content[header_start : hunk.end()].rstrip("\n"))
            blocks.append(Text(header, style="theme.text.muted"))
            remaining -= 1
            shown = min(len(body_lines), remaining)
            head, tail, omitted = _safe_lines(body, max_lines=shown)
            omitted_total += omitted or 0
            if omitted:
                omitted_source.extend(body_lines[len(head) : len(body_lines) - len(tail)])
            if head:
                blocks.append(render_diff_rows(head, int(hunk.group(1))))
            if tail:
                blocks.append(
                    render_diff_rows(tail, int(hunk.group(1)) + len(head) + (omitted or 0))
                )
            remaining -= len(head) + len(tail)
        if omitted_total:
            blocks.append(
                _elision_text(
                    omitted_total,
                    "..." if not glyphs_enabled else _ELISION_GLYPH,
                    overflow_ref,
                    "\n".join(omitted_source),
                )
            )
        return Group(*blocks) if blocks else None
    preview_start = start_line if isinstance(start_line, int) and start_line > 0 else 1
    preview = _build_write_preview(
        bare,
        preview_path,
        content,
        width=width,
        terminal_bg_is_light=terminal_bg_is_light,
        overflow_ref=overflow_ref,
        glyphs_enabled=glyphs_enabled,
        start_line=preview_start,
    )
    if preview is None:
        return None
    return (
        Group(Text("  (snippet)", style="theme.text.muted"), preview)
        if is_diff or is_relative_snippet
        else preview
    )


def build_edit_preview(
    tool_name: str,
    input_dict: dict[str, object] | PreviewPayload,
    *,
    width: int,
    terminal_bg_is_light: bool | None,
    overflow_ref: str | None = None,
    glyphs_enabled: bool = True,
    diff_fills: tuple[str, str] | None = None,
) -> RenderableType | None:
    """Return a rich renderable previewing the edit described by ``input_dict``.

    Returns ``None`` for tools without content (e.g. ``exec``) or empty
    payloads so the caller can skip the print cleanly.

    The renderable is a :class:`rich.syntax.Syntax` (write-style tools)
    or a :class:`rich.console.Group` (edit-style tools with multiple
    blocks). The caller (``ParallelDisplay``) prints the renderable
    through its own Console so the same width / quiet / sanitization
    policies apply. No I/O happens here -- including no env reads and
    no terminal probing: the caller resolves the background once and
    passes the answer in.

    Parameters:
        tool_name: Raw tool name from the event (MCP / alias prefixes
            are stripped internally).
        input_dict: The tool-call input payload.
        width: Effective console width.
        terminal_bg_is_light: ``True`` when the terminal background is
            light, ``False`` when dark, ``None`` when undetermined.
            ``None`` selects the fixed-RGB palette proven safe on both
            black and white backgrounds; the value also selects the
            diff-marker styles.

    Returns:
        A rich renderable, or ``None`` when there is nothing to preview.
    """
    bare = _normalize_tool_name(tool_name)
    canonical: PreviewPayload | None
    if isinstance(input_dict, PreviewPayload):
        canonical = input_dict
    else:
        tool_envelope: dict[str, object] = (
            input_dict
            if any(key in input_dict for key in ("input", "args", "arguments"))
            else {"input": input_dict}
        )
        canonical = payload_from_tool_event(tool_name, tool_envelope)
    if canonical is None:
        return None
    path = canonical.path
    edits_obj = canonical.hunks
    content_obj = canonical.content
    start_line = canonical.start_line or 1
    if isinstance(content_obj, str) and bare == "read_file":
        try:
            read_envelope: object = json.loads(content_obj)
        except (TypeError, ValueError):
            read_envelope = None
        if isinstance(read_envelope, dict):
            envelope_content = read_envelope.get("content")
            if isinstance(envelope_content, str):
                content_obj = envelope_content
                envelope_start = read_envelope.get("line_start", start_line)
                start_line = envelope_start if isinstance(envelope_start, int) else start_line
    if edits_obj:
        return _build_edit_preview(
            path,
            [
                {
                    "oldText": hunk.old_text,
                    "newText": hunk.new_text,
                    "start_line": hunk.start_line,
                    "label": hunk.label,
                }
                for hunk in edits_obj
            ],
            width=width,
            terminal_bg_is_light=terminal_bg_is_light,
            diff_fills=diff_fills,
            overflow_ref=overflow_ref,
            glyphs_enabled=glyphs_enabled,
        )
    if isinstance(content_obj, str) and bare in {"grep_files", "search_files"}:
        payload_input = input_dict if isinstance(input_dict, dict) else {}
        raw_input = payload_input.get("input", payload_input)
        pattern = raw_input.get("pattern") if isinstance(raw_input, dict) else None
        return _build_search_result_preview(
            content_obj,
            pattern=pattern if isinstance(pattern, str) else None,
            terminal_bg_is_light=terminal_bg_is_light,
            glyphs_enabled=glyphs_enabled,
            overflow_ref=overflow_ref,
        )
    if isinstance(content_obj, str) and bare == "read_multiple_files":
        return _build_multiple_read_preview(
            content_obj,
            width=width,
            terminal_bg_is_light=terminal_bg_is_light,
            glyphs_enabled=glyphs_enabled,
            overflow_ref=overflow_ref,
        )
    if isinstance(content_obj, str) and content_obj:
        return _build_content_preview(
            bare,
            canonical,
            content_obj,
            path,
            start_line,
            width=width,
            terminal_bg_is_light=terminal_bg_is_light,
            diff_fills=diff_fills,
            overflow_ref=overflow_ref,
            glyphs_enabled=glyphs_enabled,
        )
    return None


__all__ = [
    "build_edit_preview",
    "preview_header",
    "preview_record_text",
    "render_markdown_preview",
]
