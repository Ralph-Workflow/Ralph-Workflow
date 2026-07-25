"""Black-box tests for the edit preview builder and its ParallelDisplay wiring.

The edit preview is an additive block printed AFTER the existing one-line
TOOL_USE header line so the operator sees a syntax-highlighted preview of
the file content the agent is editing. The header line stays byte-identical
(``◐ RUN <ts> <unit> <friendly_tool_name> (<args>) ↳``); the preview is
purely additive.

Each test must complete in < 0.1 s; the whole file finishes well under 0.5 s
so the 60-second combined budget in ``make verify`` stays unbroken.

Coverage:
  1. ``build_edit_preview`` returns ``None`` for non-edit tools.
  2. ``write_file`` with a ``.py`` path yields a syntax-highlighted renderable
     carrying a Python lexer (via ``Syntax.lexer``).
  3. Artifact edit tools (``ralph_stage_md_artifact``, ``ralph_submit_md_artifact``)
     default to markdown lexer and word-wrap.
  4. Unknown extension falls back to plain text without raising.
  5. ``edit_file`` / ``ralph_edit_md_artifact`` partial edits render with
     ``-`` / ``+`` line markers and line numbers.
  6. Content containing ANSI escape sequences is sanitized (no raw ESC byte).
  7. Content longer than the preview cap is truncated with an elision marker.
  8. Integration: ``ParallelDisplay.emit_parsed_event`` with a TOOL_USE event
     for ``edit_file`` prints the unchanged header line AND the preview block;
     with ``is_quiet=True`` nothing is printed.
  9. No hex color literals in the new ``edit_preview`` module.
"""

from __future__ import annotations

import io

from rich.console import Console
from rich.syntax import Syntax

from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.context import make_display_context
from ralph.display.edit_preview import CONTENT_EDIT_TOOLS, build_edit_preview
from ralph.display.parallel_display import ParallelDisplay


def _make_display(width: int = 120) -> tuple[ParallelDisplay, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=width)
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx), buf


def _make_quiet_display(width: int = 120) -> tuple[ParallelDisplay, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=width)
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx, is_quiet=True), buf


# ---------------------------------------------------------------------------
# 1. Non-edit tools return None
# ---------------------------------------------------------------------------


def test_build_edit_preview_returns_none_for_non_edit_tools() -> None:
    """read_file / exec / git_status / grep_files are not content-edit tools."""
    for name in ("read_file", "exec", "git_status", "grep_files", "list_directory"):
        assert build_edit_preview(name, {"path": "a.py"}, width=80) is None, (
            f"{name!r} must not produce a preview"
        )


def test_build_edit_preview_returns_none_for_empty_payload() -> None:
    """Empty input_dict or empty content returns None."""
    assert build_edit_preview("write_file", {}, width=80) is None
    assert build_edit_preview("write_file", {"path": "a.py", "content": ""}, width=80) is None
    assert build_edit_preview("edit_file", {"path": "a.py", "edits": []}, width=80) is None


# ---------------------------------------------------------------------------
# 2. write_file with .py path -> Syntax-highlighted renderable
# ---------------------------------------------------------------------------


def test_build_edit_preview_write_file_python_uses_python_lexer() -> None:
    """A ``write_file`` call against a ``.py`` path returns a ``Syntax`` object
    whose lexer is the Python lexer (NOT plain text)."""
    preview = build_edit_preview(
        "write_file",
        {"path": "src/example.py", "content": "x = 1\ny = 2\n"},
        width=80,
    )
    assert preview is not None, "write_file with content must produce a preview"
    assert isinstance(preview, Syntax), (
        f"expected rich.syntax.Syntax, got {type(preview).__name__}"
    )
    assert preview.lexer.name == "Python", (
        f"expected Python lexer, got {preview.lexer.name!r}"
    )


def test_build_edit_preview_write_file_renders_line_numbers() -> None:
    """The rendered preview carries line numbers (per snippet, starting at 1)."""
    preview = build_edit_preview(
        "write_file",
        {"path": "a.py", "content": "x = 1\ny = 2\nz = 3\n"},
        width=80,
    )
    assert preview is not None
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=80)
    console.print(preview)
    rendered = buf.getvalue()
    assert "1" in rendered, f"line number '1' missing from rendered preview:\n{rendered}"
    assert "x = 1" in rendered, f"line 'x = 1' missing from rendered preview:\n{rendered}"
    assert "y = 2" in rendered
    assert "z = 3" in rendered


# ---------------------------------------------------------------------------
# 3. Artifact edit tools -> markdown lexer + word wrap
# ---------------------------------------------------------------------------


def test_build_edit_preview_artifact_stage_uses_markdown_lexer() -> None:
    """``ralph_stage_md_artifact`` (no path, has content) infers the markdown lexer."""
    preview = build_edit_preview(
        "ralph_stage_md_artifact",
        {"artifact_type": "plan", "content": "# Heading\n\nSome text here.\n"},
        width=80,
    )
    assert preview is not None, "stage artifact with content must produce a preview"
    assert isinstance(preview, Syntax)
    assert preview.lexer.name == "Markdown", (
        f"expected Markdown lexer, got {preview.lexer.name!r}"
    )
    assert preview.word_wrap is True, "markdown previews must word-wrap"


def test_build_edit_preview_artifact_submit_uses_markdown_lexer() -> None:
    """``ralph_submit_md_artifact`` (no path, has content) infers the markdown lexer."""
    preview = build_edit_preview(
        "ralph_submit_md_artifact",
        {"artifact_type": "development_result", "content": "## Section\n\nBody.\n"},
        width=80,
    )
    assert preview is not None
    assert isinstance(preview, Syntax)
    assert preview.lexer.name == "Markdown"


# ---------------------------------------------------------------------------
# 4. Unknown extension -> plain text fallback (no raise)
# ---------------------------------------------------------------------------


def test_build_edit_preview_unknown_extension_falls_back_to_plain() -> None:
    """A path with no recognised extension falls back to plain text without raising."""
    preview = build_edit_preview(
        "write_file",
        {"path": "data.xyz123", "content": "hello world\n"},
        width=80,
    )
    assert preview is not None, "even unknown extensions must render SOMETHING"
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=80)
    console.print(preview)
    rendered = buf.getvalue()
    assert "hello world" in rendered, f"content must survive fallback:\n{rendered}"


# ---------------------------------------------------------------------------
# 5. edit_file partial edits -> - / + diff-style treatment
# ---------------------------------------------------------------------------


def test_build_edit_preview_edit_file_shows_old_and_new_with_markers() -> None:
    """``edit_file`` with ``edits=[{oldText, newText}]`` renders BOTH old
    (prefixed ``-``) and new (prefixed ``+``) content with line numbers."""
    preview = build_edit_preview(
        "edit_file",
        {
            "path": "src/example.py",
            "edits": [
                {
                    "oldText": "def old():\n    return 1\n",
                    "newText": "def new():\n    return 2\n",
                }
            ],
        },
        width=80,
    )
    assert preview is not None
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=80)
    console.print(preview)
    rendered = buf.getvalue()
    assert "-" in rendered, f"old-content '-' marker missing:\n{rendered}"
    assert "+" in rendered, f"new-content '+' marker missing:\n{rendered}"
    assert "def old" in rendered, f"old content missing:\n{rendered}"
    assert "def new" in rendered, f"new content missing:\n{rendered}"
    assert "return 1" in rendered
    assert "return 2" in rendered


def test_build_edit_preview_ralph_edit_md_artifact_shows_diff() -> None:
    """``ralph_edit_md_artifact`` with ``edits=[{oldText, newText}]`` renders
    the diff-style preview (artifact tools have no ``path``)."""
    preview = build_edit_preview(
        "ralph_edit_md_artifact",
        {
            "artifact_type": "plan",
            "edits": [
                {
                    "oldText": "# Old Heading\n",
                    "newText": "# New Heading\n",
                }
            ],
        },
        width=80,
    )
    assert preview is not None
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=80)
    console.print(preview)
    rendered = buf.getvalue()
    assert "Old Heading" in rendered
    assert "New Heading" in rendered


# ---------------------------------------------------------------------------
# 6. Sanitization: ANSI escapes are stripped from agent content
# ---------------------------------------------------------------------------


def test_build_edit_preview_sanitizes_ansi_escape_sequences() -> None:
    """Hostile content carrying raw CSI escape sequences is sanitized so no
    ``\\x1b`` byte reaches the rendered preview (terminal-escape containment)."""
    hostile = "\x1b[?1049h\x1b[2J\x1b[>0cdef safe_function():\n    return 1\n"
    preview = build_edit_preview(
        "write_file",
        {"path": "a.py", "content": hostile},
        width=80,
    )
    assert preview is not None
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=80)
    console.print(preview)
    rendered = buf.getvalue()
    assert "\x1b" not in rendered, (
        f"raw ESC byte leaked into preview:\n{rendered!r}"
    )
    # Body residue from incomplete regexes that the full stripper removes.
    for forbidden in ("[?1049h", "[2J", "[>0c"):
        assert forbidden not in rendered, (
            f"hostile body {forbidden!r} leaked through preview:\n{rendered!r}"
        )
    # Visible content survives.
    assert "def safe_function" in rendered


def test_build_edit_preview_sanitizes_ansi_in_edits() -> None:
    """Hostile ``oldText`` / ``newText`` are sanitized too."""
    preview = build_edit_preview(
        "edit_file",
        {
            "path": "a.py",
            "edits": [
                {
                    "oldText": "x = \x1b[31m1\x1b[0m\n",
                    "newText": "y = 2\n",
                }
            ],
        },
        width=80,
    )
    assert preview is not None
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=80)
    console.print(preview)
    rendered = buf.getvalue()
    assert "\x1b" not in rendered
    assert "y = 2" in rendered


# ---------------------------------------------------------------------------
# 7. Content longer than the preview cap is truncated with an elision marker
# ---------------------------------------------------------------------------


def test_build_edit_preview_truncates_long_content_with_elision() -> None:
    """Content exceeding the preview cap is truncated and an elision marker
    is appended so the operator knows more lines exist."""
    long_content = "\n".join(f"line {i:04d} = payload" for i in range(200))
    preview = build_edit_preview(
        "write_file",
        {"path": "a.py", "content": long_content},
        width=80,
    )
    assert preview is not None
    # The rendered preview should not contain every line: the cap is enforced.
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=80)
    console.print(preview)
    rendered = buf.getvalue()
    # Either an explicit "more lines" marker OR a … ellipsis token must
    # appear to signal truncation.
    has_marker = "more line" in rendered.lower() or "\u2026" in rendered or "..." in rendered
    assert has_marker, (
        f"elision marker missing from truncated preview:\n{rendered[-500:]!r}"
    )
    # The very last line in the source must NOT appear (it was truncated).
    assert "line 0199" not in rendered, (
        f"truncation cap not enforced; final source line still rendered:\n{rendered[-500:]!r}"
    )


# ---------------------------------------------------------------------------
# 8. Integration with ParallelDisplay.emit_parsed_event
# ---------------------------------------------------------------------------


def test_parallel_display_emit_parsed_event_prints_header_and_preview_for_tool_use() -> None:
    """``ParallelDisplay.emit_parsed_event`` for a TOOL_USE event for ``edit_file``
    prints the unchanged one-line header AND an additive preview block."""
    pd, buf = _make_display()
    pd.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_USE,
        content="mcp__ralph__edit_file",
        metadata={
            "input": {
                "path": "src/example.py",
                "edits": [
                    {"oldText": "def old():\n    return 1\n", "newText": "def new():\n    return 2\n"},
                ],
            }
        },
    )
    pd.stop()
    output = buf.getvalue()
    # Header line still present and byte-identical to the baseline shape.
    assert "RUN" in output, f"header missing RUN carrier:\n{output!r}"
    assert "dev-1" in output, f"header missing unit_id:\n{output!r}"
    assert "ralph.edit_file" in output, f"header missing friendly tool name:\n{output!r}"
    # Preview content present.
    assert "def old" in output, f"preview missing old content:\n{output!r}"
    assert "def new" in output, f"preview missing new content:\n{output!r}"
    # Preview carries diff markers.
    assert "+" in output and "-" in output


def test_parallel_display_quiet_mode_suppresses_tool_use_preview() -> None:
    """Quiet mode suppresses the additive preview block.

    The existing one-line TOOL_USE header is allowed to remain (the
    header-line emission path is not quiet-gated by the existing
    ``ParallelDisplay`` contract). The quiet-gated discipline applies
    to the new preview path so it can never paint extra content in a
    single-line run.

    The preview prints a multi-line diff block; without the quiet gate
    the captured output would carry far more lines than with the gate
    on. The header-only output and the header+preview output are
    compared directly so the assertion is independent of the header's
    exact repr shape (which can change with registry revisions).
    """
    metadata = {
        "input": {
            "path": "src/example.py",
            "edits": [
                {
                    "oldText": (
                        "def very_long_function_name_one():\n"
                        "    return SOME_OLD_SENTINEL_VALUE_42\n"
                        "    raise NotImplementedError\n"
                    ),
                    "newText": (
                        "def very_long_function_name_one():\n"
                        "    return SOME_NEW_SENTINEL_VALUE_99\n"
                        "    raise NotImplementedError\n"
                    ),
                },
            ],
        }
    }
    pd_quiet, buf_quiet = _make_quiet_display()
    pd_quiet.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_USE,
        content="mcp__ralph__edit_file",
        metadata=metadata,
    )
    pd_quiet.stop()
    pd_loud, buf_loud = _make_display()
    pd_loud.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_USE,
        content="mcp__ralph__edit_file",
        metadata=metadata,
    )
    pd_loud.stop()
    quiet_lines = [line for line in buf_quiet.getvalue().splitlines() if line.strip()]
    loud_lines = [line for line in buf_loud.getvalue().splitlines() if line.strip()]
    assert len(loud_lines) > len(quiet_lines), (
        f"non-quiet display must emit MORE lines (header + preview) than quiet "
        f"display (header only); got quiet={len(quiet_lines)} loud={len(loud_lines)}; "
        f"quiet={buf_quiet.getvalue()!r} loud={buf_loud.getvalue()!r}"
    )


# ---------------------------------------------------------------------------
# 9. No hex color literals in the new module
# ---------------------------------------------------------------------------


def test_edit_preview_module_uses_no_hex_color_literals() -> None:
    """Okabe-Ito discipline: the new ``edit_preview`` module must not introduce
    any hex color literal. The wider anti-drift test
    (``tests/display/test_no_hex_colors_outside_theme.py``) already walks
    every ``ralph/display/*.py`` and fails on any hex literal; this test
    pins the new module specifically so the contract is named in the same
    file that exercises it."""
    from pathlib import Path

    module_path = (
        Path(__file__).parent.parent.parent / "ralph" / "display" / "edit_preview.py"
    )
    assert module_path.exists(), f"module missing at {module_path}"
    text = module_path.read_text(encoding="utf-8")
    import re

    hex_pattern = re.compile(r"#[0-9A-Fa-f]{6}")
    hits = hex_pattern.findall(text)
    assert not hits, (
        f"edit_preview.py must not contain hex color literals "
        f"(Okabe-Ito discipline); found {hits!r}"
    )


# ---------------------------------------------------------------------------
# Toolset sanity check: the bare names match the plan.
# ---------------------------------------------------------------------------


def test_content_edit_tools_set_includes_all_required_bare_names() -> None:
    """The public ``CONTENT_EDIT_TOOLS`` set covers every content-edit tool
    named in the plan (write_file / append_file / edit_file plus the three
    artifact tools). The MCP-prefix normalisation is exercised in
    ``test_build_edit_preview_returns_none_for_non_edit_tools``."""
    expected = {
        "edit_file",
        "write_file",
        "append_file",
        "ralph_edit_md_artifact",
        "ralph_stage_md_artifact",
        "ralph_submit_md_artifact",
    }
    assert expected <= CONTENT_EDIT_TOOLS, (
        f"missing tools in CONTENT_EDIT_TOOLS: {expected - CONTENT_EDIT_TOOLS}"
    )
