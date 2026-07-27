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

* Language inference: explicit suffix map first (``py`` -> Python,
  ``md`` -> Markdown, ``toml`` -> TOML, ``yaml``/``yml`` -> YAML,
  ``json`` -> JSON, ``js``/``ts`` -> JavaScript/TypeScript, ``sh`` ->
  Bash, ``rs`` -> Rust, ``go`` -> Go); fallback to
  ``pygments.lexers.get_lexer_for_filename`` for everything else;
  ``ClassNotFound`` and any other pygments exception degrade to
  ``"text"`` (plain text) rather than raising. Artifact tools that
  carry no ``path`` key (``ralph_edit_md_artifact``,
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
  ``line_numbers=True``; if the lexer is markdown, ``word_wrap=True``.

* Edit-style tools (``edit_file`` / ``ralph_edit_md_artifact``):
  every entry in ``edits`` is rendered as a ``- old / + new`` diff
  with the new content syntax-highlighted under ``line_numbers=True``
  starting at 1 (snippet-relative; the display path has the tool
  input only, not the file on disk, so absolute file line numbers
  are unavailable). The old lines use the ``theme.status.error``
  carrier (vermillion) and the new lines use the
  ``theme.status.success`` carrier (bluish-green) so the diff
  remains readable when color is disabled: the literal ``-`` and
  ``+`` markers carry the meaning.

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

Highlight colours are NOT fixed RGB. Every ``Syntax`` renderable this
module builds is themed via
:func:`ralph.display.theme.syntax_theme_for_background`, which returns
an ANSI theme (``ansi_dark`` / ``ansi_light``). ANSI themes colour
tokens with the terminal's own 16-colour palette and paint no
background, so the preview inherits whatever colour scheme the
operator configured and always contrasts with their background.

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
also clear WCAG contrast on a light terminal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

from pygments.lexers import get_lexer_for_filename
from pygments.util import ClassNotFound
from rich.console import Group
from rich.syntax import Syntax
from rich.text import Text

from ralph.display.line_sanitizer import strip_terminal_control
from ralph.display.theme import (
    SYNTAX_BACKGROUND_TRANSPARENT,
    pick_status_styles,
    syntax_theme_for_background,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rich.console import RenderableType

#: Bare tool names that produce a content preview. The MCP prefix
#: ``mcp__ralph__`` and the alias prefix ``ralph.`` are stripped
#: before the lookup so ``mcp__ralph__write_file`` and
#: ``ralph.write_file`` both resolve to ``write_file``.
CONTENT_EDIT_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "edit_file",
        "write_file",
        "append_file",
        "ralph_edit_md_artifact",
        "ralph_stage_md_artifact",
        "ralph_submit_md_artifact",
        "read_file",
    }
)

#: MCP and alias prefixes stripped from a tool name before the
#: ``CONTENT_EDIT_TOOLS`` lookup. Order matters: the longer
#: ``mcp__ralph__`` prefix is checked first.
_MCP_RALPH_PREFIX: Final[str] = "mcp__ralph__"
_RA_PREFIX: Final[str] = "ralph."

#: Suffix -> lexer name map for the most common file types. Anything
#: not in this map falls back to ``pygments.lexers.get_lexer_for_filename``
#: (and ultimately to plain text on lookup failure).
_SUFFIX_LEXER: Final[dict[str, str]] = {
    ".py": "python",
    ".md": "markdown",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".sh": "bash",
    ".rs": "rust",
    ".go": "go",
    ".html": "html",
    ".css": "css",
    ".xml": "xml",
}

#: Maximum number of lines a preview may show. Anything longer is
#: truncated and replaced with a muted elision line so the preview
#: stays scannable and the panel cannot grow without bound.
_MAX_PREVIEW_LINES: Final[int] = 40

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


def _lexer_for_path(path: str) -> str:
    """Return the pygments lexer name for ``path`` based on its extension.

    Unknown extensions fall back to ``pygments.lexers.get_lexer_for_filename``
    and ultimately to ``"text"`` (plain text) so the preview never
    raises on an unexpected extension.

    The returned value is always a pygments *alias* (``"python"``,
    ``"markdown"``, ``"text"``) rather than a display name, because
    ``rich.syntax.Syntax`` resolves its lexer argument through
    ``get_lexer_by_name``, which only accepts aliases. Returning the
    display name (``"Python"``) made every fallback-path preview
    silently degrade to unhighlighted plain text.
    """
    # Cheap suffix scan first: avoids pygments lookup for the common
    # types and keeps the lexer name stable across pygments versions.
    lowered = path.lower()
    for suffix, alias in _SUFFIX_LEXER.items():
        if lowered.endswith(suffix):
            return alias
    try:
        lexer: object = get_lexer_for_filename(path)
    except ClassNotFound:
        return "text"
    except Exception:
        return "text"
    aliases = cast("Sequence[str]", getattr(lexer, "aliases", ()))
    return aliases[0] if aliases else "text"


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


def _safe_lines(content: str, *, max_lines: int) -> tuple[list[str], int | None]:
    """Sanitize ``content`` and split into at most ``max_lines`` lines.

    Returns a ``(lines, omitted_count)`` pair. ``omitted_count`` is
    ``None`` when no truncation was needed. Terminal-escape sequences
    are stripped from every line so a hostile payload cannot paint
    the operator's terminal (R-1).
    """
    sanitized = strip_terminal_control(content)
    if not sanitized:
        return [], None
    raw_lines = sanitized.splitlines()
    if len(raw_lines) <= max_lines:
        return raw_lines, None
    omitted = len(raw_lines) - max_lines
    return raw_lines[:max_lines], omitted


def _elision_text(omitted: int) -> Text:
    """Build a muted ``… (N more lines)`` Text for the truncation marker."""
    noun = "line" if omitted == 1 else "lines"
    text = Text()
    text.append(f"{_ELISION_GLYPH} ({omitted} more {noun})", style=_ELISION_STYLE)
    return text


def _make_syntax(
    body: str,
    lexer_name: str,
    *,
    is_markdown: bool,
    terminal_bg_is_light: bool | None,
) -> Syntax:
    """Build the themed ``Syntax`` renderable used by both preview shapes.

    ``background_color`` is transparent and the theme is an ANSI theme,
    so the block never paints over the terminal background and its token
    colours come from the operator's own 16-colour scheme.
    """
    return Syntax(
        body,
        lexer_name,
        theme=syntax_theme_for_background(terminal_bg_is_light),
        line_numbers=True,
        word_wrap=is_markdown,
        background_color=SYNTAX_BACKGROUND_TRANSPARENT,
    )


def _build_write_preview(
    tool_name: str,
    path: str | None,
    content: str,
    *,
    width: int,
    terminal_bg_is_light: bool | None,
) -> RenderableType | None:
    """Build a ``Syntax``-based preview for ``write_file`` / ``append_file``
    and the two artifact-stage / artifact-submit tools."""
    del tool_name, width  # reserved for future width-aware wrapping; currently unused
    if not content:
        return None
    lexer_name = "markdown" if path is None else _lexer_for_path(path)
    is_markdown = _is_markdown_lexer(lexer_name)
    lines, omitted = _safe_lines(content, max_lines=_MAX_PREVIEW_LINES)
    if not lines:
        return None
    syntax = _make_syntax(
        "\n".join(lines),
        lexer_name,
        is_markdown=is_markdown,
        terminal_bg_is_light=terminal_bg_is_light,
    )
    if omitted is None:
        return syntax
    return Group(syntax, _elision_text(omitted))


def _build_edit_preview(
    path: str | None,
    edits: list[dict[str, object]],
    *,
    width: int,
    terminal_bg_is_light: bool | None,
) -> RenderableType | None:
    """Build a diff-style preview for ``edit_file`` / ``ralph_edit_md_artifact``.

    Each edit renders both old (prefixed ``-``, ``theme.status.error``
    style) and new (prefixed ``+``, ``theme.status.success`` style)
    content with snippet-relative line numbers starting at 1. The
    new content is additionally syntax-highlighted via a nested
    ``Syntax`` renderable so partial edits show real lexer coloring.

    Edit tools without a ``path`` (``ralph_edit_md_artifact``) default
    the lexer to ``"markdown"`` because the artifact content is
    always a markdown document.
    """
    del width  # reserved for future width-aware wrapping; currently unused
    if not edits:
        return None
    lexer_name = "markdown" if path is None else _lexer_for_path(path or "")
    is_markdown = _is_markdown_lexer(lexer_name)
    old_style = _diff_marker_style(_DIFF_OLD_STATUS, terminal_bg_is_light=terminal_bg_is_light)
    new_style = _diff_marker_style(_DIFF_NEW_STATUS, terminal_bg_is_light=terminal_bg_is_light)
    blocks: list[RenderableType] = []
    total_omitted = 0
    for edit in edits:
        old_text = str(edit.get("oldText", "") or "")
        new_text = str(edit.get("newText", "") or "")
        if not old_text and not new_text:
            continue
        old_safe = strip_terminal_control(old_text)
        new_safe = strip_terminal_control(new_text)
        # Old block: prefixed "-" lines, dimmed, no lexer (raw diff).
        if old_safe:
            old_lines, old_omitted = _safe_lines(old_safe, max_lines=_MAX_PREVIEW_LINES)
            old_block = Text()
            for index, line in enumerate(old_lines, start=1):
                old_block.append(f"{index:>3} - {line}\n", style=old_style)
            if old_omitted is not None:
                old_block.append_text(_elision_text(old_omitted))
            # Drop the trailing newline: Console.print already ends the
            # renderable with one, and the extra blank line between the
            # ``-`` and ``+`` halves makes a two-line edit read as four
            # disconnected fragments.
            old_block.rstrip()
            blocks.append(old_block)
            total_omitted += old_omitted or 0
        # New block: Syntax-highlighted with line numbers starting at 1.
        if new_safe:
            new_lines, new_omitted = _safe_lines(new_safe, max_lines=_MAX_PREVIEW_LINES)
            if new_lines:
                new_syntax = _make_syntax(
                    "\n".join(new_lines),
                    lexer_name,
                    is_markdown=is_markdown,
                    terminal_bg_is_light=terminal_bg_is_light,
                )
                # Wrap the new block in a Text frame so the leading
                # ``+`` marker on the first line is visible without
                # disturbing the Syntax line-number column. The diff
                # prefix carries the meaning; the per-line new content
                # remains syntax-highlighted. No trailing newline:
                # ``Group`` already places the frame and the Syntax
                # block on separate lines.
                frame = Text()
                frame.append(f"+ ({path or 'edit'})", style=new_style)
                blocks.append(Group(frame, new_syntax))
                total_omitted += new_omitted or 0
    if not blocks:
        return None
    return Group(*blocks)


def build_edit_preview(
    tool_name: str,
    input_dict: dict[str, object],
    *,
    width: int,
    terminal_bg_is_light: bool | None = None,
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
            light, ``False`` when dark, ``None`` when undetermined
            (treated as dark). Selects the ANSI syntax theme and the
            diff-marker styles.

    Returns:
        A rich renderable, or ``None`` when there is nothing to preview.
    """
    bare = _normalize_tool_name(tool_name)
    if bare not in CONTENT_EDIT_TOOLS:
        return None
    if not isinstance(input_dict, dict) or not input_dict:
        return None
    path_obj = input_dict.get("path")
    path = path_obj if isinstance(path_obj, str) and path_obj else None
    edits_obj = input_dict.get("edits")
    content_obj = input_dict.get("content")
    if isinstance(edits_obj, list) and edits_obj:
        return _build_edit_preview(
            path,
            [e for e in edits_obj if isinstance(e, dict)],
            width=width,
            terminal_bg_is_light=terminal_bg_is_light,
        )
    if isinstance(content_obj, str) and content_obj:
        return _build_write_preview(
            bare,
            path,
            content_obj,
            width=width,
            terminal_bg_is_light=terminal_bg_is_light,
        )
    return None


__all__ = [
    "build_edit_preview",
]
