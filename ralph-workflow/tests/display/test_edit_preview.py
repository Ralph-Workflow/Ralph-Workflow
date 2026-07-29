"""Black-box tests for edit-preview rendering and live display wiring."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text

from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.context import make_display_context
from ralph.display.edit_preview import (
    build_edit_preview as _build_edit_preview,
)
from ralph.display.edit_preview import (
    preview_header,
    preview_record_text,
    render_markdown_preview,
)
from ralph.display.language_inference import lexer_for_path
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.preview_payload import PreviewPayload, payload_from_tool_event


def build_edit_preview(
    tool_name: str,
    input_dict: dict[str, object] | PreviewPayload,
    *,
    width: int,
    terminal_bg_is_light: bool | None = False,
    overflow_ref: str | None = None,
    glyphs_enabled: bool = True,
) -> object:
    """Keep legacy test fixtures explicit about their dark test terminal."""
    return _build_edit_preview(
        tool_name,
        input_dict,
        width=width,
        terminal_bg_is_light=terminal_bg_is_light,
        overflow_ref=overflow_ref,
        glyphs_enabled=glyphs_enabled,
    )

def _make_display(width: int = 120) -> tuple[ParallelDisplay, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=width)
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx), buf

def _make_truecolor_display(width: int = 120) -> tuple[ParallelDisplay, io.StringIO]:
    """Return a live display whose output retains truecolor escape sequences."""
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        width=width,
        highlight=False,
    )
    return ParallelDisplay(make_display_context(console=console, env={})), buf

def _make_quiet_display(width: int = 120) -> tuple[ParallelDisplay, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=width)
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx, is_quiet=True), buf

def test_build_edit_preview_returns_none_for_non_content_tools() -> None:
    """Commands without file content do not produce a preview."""
    for name in ("exec", "git_status", "grep_files", "list_directory"):
        assert build_edit_preview(name, {"path": "a.py"}, width=80) is None, (
            f"{name!r} must not produce a preview"
        )

def test_build_edit_preview_read_file_uses_path_lexer() -> None:
    """Read results get the same file-content preview as writes (S-3)."""
    preview = build_edit_preview(
        "read_file",
        {"path": "ralph/display/status_bar.py", "content": "def render():\n    return 1\n"},
        width=80,
    )
    assert isinstance(preview, Syntax)
    assert preview.lexer.name == "Python"

def test_read_file_regression_snippet_uses_requested_path_lexer() -> None:
    """DA-001: relative gutters do not turn an unwindowed Python read into a diff."""
    preview = build_edit_preview(
        "read_file",
        {"path": "a.py", "content": "def render():\n    return 1\n", "is_snippet": True},
        width=80,
    )
    assert isinstance(preview, Group)
    assert any(
        isinstance(renderable, Text) and "(snippet)" in renderable.plain
        for renderable in preview.renderables
    )
    syntax = next(
        renderable for renderable in preview.renderables if isinstance(renderable, Syntax)
    )
    assert syntax.lexer.name == "Python"


def test_build_edit_preview_returns_none_for_empty_payload() -> None:
    """Empty input_dict or empty content returns None."""
    assert build_edit_preview("write_file", {}, width=80) is None
    assert build_edit_preview("write_file", {"path": "a.py", "content": ""}, width=80) is None
    assert build_edit_preview("edit_file", {"path": "a.py", "edits": []}, width=80) is None


def test_native_json_payload_and_patch_normalize_without_parser_identity() -> None:
    """S-1: documented parser envelopes normalize native editor calls."""
    write = payload_from_tool_event("Write", {"args": '{"file_path":"a.py","content":"x = 1"}'})
    assert write is not None and write.path == "a.py" and write.operation == "write"
    patch = payload_from_tool_event(
        "apply_patch",
        {"input": {"patch": "@@ -3,1 +3,1 @@\n-old\n+new\n"}},
    )
    assert patch is not None and patch.hunks[0].start_line == 3
    assert patch.hunks[0].label == "hunk 1 (line 3)"
    preview = build_edit_preview("apply_patch", patch, width=80)
    assert preview is not None


def test_preview_payload_accepts_gemini_documented_literal_args() -> None:
    """S-1: Gemini's documented stringified argument mapping stays previewable."""
    payload = payload_from_tool_event(
        "Write", {"args": "{'file_path': 'a.py', 'content': 'x = 1'}"}
    )
    assert payload is not None
    assert payload.path == "a.py"
    assert payload.content == "x = 1"


def test_build_edit_preview_binary_content_degrades_to_note() -> None:
    """S-2: binary payloads are never rendered as raw bytes."""
    preview = build_edit_preview(
        "read_file", {"path": "image.bin", "content": "\x00unsafe"}, width=80
    )
    assert preview is not None
    rendered = io.StringIO()
    Console(file=rendered, force_terminal=False, color_system=None, width=80).print(preview)
    assert "binary content omitted" in rendered.getvalue()
    assert "unsafe" not in rendered.getvalue()


def test_preview_payload_rejects_unknown_tools_and_invalid_json() -> None:
    """S-1: unknown names and malformed JSON never trigger content guessing."""
    assert payload_from_tool_event("unrecognized_editor", {"args": {"content": "x"}}) is None
    assert payload_from_tool_event("Write", {"args": "not JSON"}) is None


def test_build_edit_preview_read_multiple_files_renders_each_file_with_its_lexer() -> None:
    """S-2: multi-file read results preserve individual file boundaries and lexers."""
    preview = build_edit_preview(
        "read_multiple_files",
        {
            "content": '{"files":[{"path":"a.py","content":"x = 1","line_start":20},{"path":"settings.yaml","content":"key: value"}]}'
        },
        width=80,
    )
    assert isinstance(preview, Group)
    rendered = io.StringIO()
    Console(file=rendered, force_terminal=False, color_system=None, width=80).print(preview)
    assert "a.py" in rendered.getvalue()
    assert "settings.yaml" in rendered.getvalue()
    assert re.search(r"\b20\s+x = 1", rendered.getvalue())


def test_partial_read_envelope_uses_real_window_line_number() -> None:
    """A read result envelope starts the gutter at its source window."""
    preview = build_edit_preview(
        "read_file",
        {"path": "a.py", "content": '{"content":"x = 1","line_start":17}'},
        width=80,
    )
    assert isinstance(preview, Syntax)
    assert preview.start_line == 17


def test_build_edit_preview_write_file_python_uses_python_lexer() -> None:
    """A ``write_file`` call against a ``.py`` path returns a ``Syntax`` object
    whose lexer is the Python lexer (NOT plain text)."""
    preview = build_edit_preview(
        "write_file",
        {"path": "src/example.py", "content": "x = 1\ny = 2\n"},
        width=80,
    )
    assert preview is not None, "write_file with content must produce a preview"
    assert isinstance(preview, Syntax), f"expected rich.syntax.Syntax, got {type(preview).__name__}"
    assert preview.lexer.name == "Python", f"expected Python lexer, got {preview.lexer.name!r}"


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


def test_build_edit_preview_artifact_stage_uses_markdown_lexer() -> None:
    """``ralph_stage_md_artifact`` (no path, has content) infers the markdown lexer."""
    preview = build_edit_preview(
        "ralph_stage_md_artifact",
        {"artifact_type": "plan", "content": "# Heading\n\nSome text here.\n"},
        width=80,
    )
    assert preview is not None, "stage artifact with content must produce a preview"
    assert isinstance(preview, Markdown)


def test_build_edit_preview_artifact_submit_uses_markdown_lexer() -> None:
    """``ralph_submit_md_artifact`` (no path, has content) infers the markdown lexer."""
    preview = build_edit_preview(
        "ralph_submit_md_artifact",
        {"artifact_type": "development_result", "content": "## Section\n\nBody.\n"},
        width=80,
    )
    assert preview is not None
    assert isinstance(preview, Markdown)


def test_language_inference_supports_compound_named_and_sniffed_inputs() -> None:
    """S-3: representative suffixes, names, and safe content sniffing resolve."""
    cases = {
        "types.d.ts": "typescript",
        "component.spec.ts": "typescript",
        "styles.module.css": "css",
        "values.yaml.j2": "yaml",
        "nginx.conf.tmpl": "ini",
        "deploy.sh.tmpl": "bash",
        "Dockerfile": "docker",
        "CMakeLists.txt": "cmake",
        ".env.production": "bash",
        "main.cpp": "cpp",
        "service.kt": "kotlin",
        "schema.graphql": "graphql",
        "infra.tf": "hcl",
        "script.ps1": "powershell",
        "archive.tar.gz": "text",
    }
    for path, lexer in cases.items():
        assert lexer_for_path(path) == lexer
    assert lexer_for_path(None, "#!/usr/bin/env python3\nprint(1)\n") == "python"
    assert lexer_for_path(None, "@@ -1 +1 @@\n-old\n+new\n") == "diff"
    assert lexer_for_path(None, "<?xml version='1.0'?><root />") == "xml"
    assert lexer_for_path(None, '{"enabled": true}') == "json"


@settings(max_examples=10, deadline=None)
@given(
    path=st.one_of(st.none(), st.text(max_size=128)),
    content=st.text(max_size=1_024),
)
def test_language_inference_property_never_raises_for_arbitrary_text(
    path: str | None, content: str
) -> None:
    """S-3: arbitrary parser text always degrades to a non-empty lexer alias."""
    lexer = lexer_for_path(path, content)
    assert isinstance(lexer, str)
    assert lexer


def test_language_inference_never_raises_for_adversarial_content() -> None:
    """S-3: malformed, binary, escaped, and oversized input remains display-safe."""
    hostile = "\x1b[31m" + ("x" * 10_000) + "\x00"
    for path, content in (
        (None, ""),
        ("unknown.unrecognized", ""),
        ("unknown.unrecognized", hostile),
        ("image.png", "\x00\xff"),
    ):
        lexer = lexer_for_path(path, content)
        assert isinstance(lexer, str)
        assert lexer


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


def test_build_edit_preview_edit_file_uses_present_start_line() -> None:
    """A known edit position starts the new-content line numbers there."""
    preview = build_edit_preview(
        "edit_file",
        {
            "path": "src/example.py",
            "edits": [{"newText": "def new():\n    return 2\n", "start_line": 27}],
        },
        width=80,
    )
    assert preview is not None
    buf = io.StringIO()
    Console(file=buf, force_terminal=False, color_system=None, width=80).print(preview)
    rendered = buf.getvalue()
    assert "27" in rendered, f"known start line missing from preview:\n{rendered}"
    assert "28" in rendered, f"known subsequent line missing from preview:\n{rendered}"


def test_build_edit_preview_edit_file_marks_unknown_line_numbers_as_snippet() -> None:
    """Unknown edit positions visibly distinguish snippet-relative gutters (S-5)."""
    preview = build_edit_preview(
        "edit_file", {"path": "a.py", "edits": [{"newText": "x = 1\ny = 2\n"}]}, width=80
    )
    assert preview is not None
    rendered = io.StringIO()
    Console(file=rendered, force_terminal=False, color_system=None, width=80).print(preview)
    assert "(snippet)" in rendered.getvalue()


def test_build_edit_preview_edit_file_without_valid_start_line_starts_at_one() -> None:
    """Absent or nonpositive positions retain snippet-relative numbering."""
    for start_line in (None, False, 0, -1):
        edit: dict[str, object] = {"newText": "x = 1\ny = 2\n"}
        if start_line is not None:
            edit["start_line"] = start_line
        preview = build_edit_preview("edit_file", {"path": "a.py", "edits": [edit]}, width=80)
        assert preview is not None
        buf = io.StringIO()
        Console(file=buf, force_terminal=False, color_system=None, width=80).print(preview)
        rendered = buf.getvalue()
        assert "1" in rendered and "2" in rendered, (
            f"invalid start_line={start_line!r} must remain snippet-relative:\n{rendered}"
        )


def test_preview_header_survives_condensed_content() -> None:
    """The separate file header remains visible when its preview is elided."""
    preview = build_edit_preview(
        "write_file",
        {"path": "src/example.py", "content": "\n".join(f"line {i}" for i in range(41))},
        width=80,
    )
    assert preview is not None
    buf = io.StringIO()
    Console(file=buf, force_terminal=False, color_system=None, width=80).print(
        Group(preview_header("write_file", "src/example.py"), preview)
    )
    rendered = buf.getvalue()
    header = "  ▸ write  src/example.py"
    assert header in rendered
    assert "more line" in rendered.lower()
    assert rendered.index(header) < rendered.lower().index("more line")


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
    assert "\x1b" not in rendered, f"raw ESC byte leaked into preview:\n{rendered!r}"
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
    assert has_marker, f"elision marker missing from truncated preview:\n{rendered[-500:]!r}"
    # Middle trimming preserves the end of a long edit as well as its start.
    assert "line 0000" in rendered and "line 0199" in rendered, (
        f"middle-trim must retain both ends of the source:\n{rendered[-500:]!r}"
    )


def test_markdown_preview_regression_wraps_prose_without_wrapping_fenced_code() -> None:
    """S-1: Markdown prose wraps; fenced source does not."""
    output = _render_truecolor(
        render_markdown_preview(
            f"{'prose ' * 20}\n\n```python\n{'identifier_' * 10}\n```",
            width=80,
            terminal_bg_is_light=False,
        )
    )
    assert output.count("prose") > 2 and output.count("identifier_") < 10


def test_render_markdown_preview_uses_background_aware_fenced_code_theme() -> None:
    """S-5: fenced Markdown code uses the same fixed-RGB syntax theme as previews."""
    markdown = "# Heading\n\n```python\nx = 1\n```"
    dark = _render_truecolor(
        render_markdown_preview(markdown, width=80, terminal_bg_is_light=False)
    )
    light = _render_truecolor(
        render_markdown_preview(markdown, width=80, terminal_bg_is_light=True)
    )
    assert "x" in dark and "\x1b[" in dark
    assert dark != light


def test_build_edit_preview_multiple_hunks_share_line_budget_and_report_total_omitted() -> None:
    """S-5: a single 40-line budget preserves both hunk ends and one total count."""
    source = "\n".join(f"line {index}" for index in range(30))
    preview = build_edit_preview(
        "edit_file",
        {"path": "a.py", "edits": [{"oldText": source}, {"newText": source}]},
        width=80,
    )
    assert preview is not None
    rendered = io.StringIO()
    Console(file=rendered, force_terminal=False, color_system=None, width=80).print(preview)
    output = rendered.getvalue()
    assert output.count("more lines") == 1
    assert "line 0" in output and "line 29" in output


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
                    {
                        "oldText": "def old():\n    return 1\n",
                        "newText": "def new():\n    return 2\n",
                    },
                ],
            }
        },
    )
    pd.stop()
    output = buf.getvalue()
    assert "RUN" in output, f"header missing RUN carrier:\n{output!r}"
    assert "dev-1" in output, f"header missing unit_id:\n{output!r}"
    assert "ralph.edit_file" in output, f"header missing friendly tool name:\n{output!r}"
    assert "src/example.py" in output
    assert output.index("src/example.py") < output.index("def old")
    assert "def old" in output, f"preview missing old content:\n{output!r}"
    assert "def new" in output, f"preview missing new content:\n{output!r}"
    assert "+" in output and "-" in output


def test_parallel_display_regression_read_result_is_highlighted_once() -> None:
    """DA-003: a successful read result reaches the truecolor live preview once."""
    pd, buf = _make_truecolor_display()
    pd.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_RESULT,
        content="def render():\n    return 1\n",
        metadata={
            "tool_name": "read_file",
            "tool_path": "ralph/display/status_bar.py",
            "exit_code": 0,
        },
    )
    pd.stop()
    output = buf.getvalue()
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    assert "ralph/display/status_bar.py" in plain
    assert plain.count("def render") == 1
    assert plain.count("return 1") == 1
    assert "\x1b[38;2;" in output


def test_parallel_display_read_multiple_files_result_uses_per_file_preview() -> None:
    """DA-003: a multi-file result has one truecolor, separately lexed presentation."""
    pd, buf = _make_truecolor_display()
    pd.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_RESULT,
        content='{"files":[{"path":"a.py","content":"x = 1"},{"path":"settings.yaml","content":"key: value"}]}',
        metadata={"tool_name": "read_multiple_files", "exit_code": 0},
    )
    pd.stop()
    output = buf.getvalue()
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    assert plain.count("a.py") == 1
    assert plain.count("settings.yaml") == 1
    assert plain.count("x = 1") == 1
    assert plain.count("key: value") == 1
    assert "\x1b[38;2;" in output


def test_parallel_display_exec_diff_result_uses_diff_preview() -> None:
    """DA-002: a unified-diff exec result reaches the truecolor preview seam."""
    pd, buf = _make_truecolor_display()
    pd.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_RESULT,
        content="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n",
        metadata={"tool_name": "exec", "exit_code": 0},
    )
    pd.stop()
    output = buf.getvalue()
    assert "▸ exec" in output
    assert output.count("@@ -1 +1 @@") == 1
    assert output.count("-old") == 1
    assert output.count("+new") == 1
    assert "\x1b[38;2;" in output


def test_parallel_display_exec_non_diff_result_has_no_preview() -> None:
    """DA-002: ordinary command output remains the single inline result row."""
    pd, buf = _make_truecolor_display()
    pd.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_RESULT,
        content="plain command output",
        metadata={"tool_name": "exec", "exit_code": 0},
    )
    pd.stop()
    assert "▷ exec" not in buf.getvalue()


def test_parallel_display_records_plain_preview_and_uses_ascii_fallback(tmp_path: Path) -> None:
    """S-4: records retain plain, greppable previews even when live glyphs fall back."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=80)
    ctx = make_display_context(console=console, env={"TERM": "dumb"}, force_glyphs=False)
    display = ParallelDisplay(ctx, workspace_root=tmp_path)
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_USE,
        content="mcp__ralph__edit_file",
        metadata={
            "input": {
                "path": "a.py",
                "edits": [{"oldText": "old = 1", "newText": "new = 2", "start_line": 7}],
            }
        },
    )
    display.stop()
    output = buf.getvalue()
    assert "  > edit  a.py" in output
    assert "\u001b" not in output
    record = (tmp_path / ".agent" / "raw" / "dev-1.rendered.log").read_text(encoding="utf-8")
    assert "> edit  a.py" in record
    assert "-    7 old = 1" in record
    assert "+    7 new = 2" in record
    assert "\u001b" not in record


def test_parallel_display_quiet_mode_suppresses_tool_result_preview() -> None:
    """S-4: quiet mode suppresses result previews from the terminal."""
    display, buf = _make_quiet_display()
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_RESULT,
        content="S4_QUIET_RESULT_SENTINEL",
        metadata={"tool_name": "read_file", "tool_path": "a.py"},
    )
    display.stop()
    assert "S4_QUIET_RESULT_SENTINEL" not in buf.getvalue()


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
# 8b. Highlight colours adapt to the terminal background
# ---------------------------------------------------------------------------

_PY_SNIPPET = 'import os\n\n\ndef handler(value: int) -> str:\n    # note\n    return f"{value}"\n'


def _render_truecolor(preview: object) -> str:
    """Render with escape codes intact so colour choices are inspectable."""
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        no_color=False,
        width=80,
    )
    console.print(preview)
    return buf.getvalue()


def test_preview_emits_fixed_rgb_colours() -> None:
    """Syntax tokens use the shared, accessibility-checked palette."""
    for flag in (True, False, None):
        preview = build_edit_preview(
            "write_file",
            {"path": "a.py", "content": _PY_SNIPPET},
            width=80,
            terminal_bg_is_light=flag,
        )
        assert "38;2;" in _render_truecolor(preview)


def test_diff_preview_regression_remains_transparent_on_all_backgrounds() -> None:
    """S-2: diff previews use structural markers, never contrast-breaking fills."""
    payloads = (
        ("edit_file", {"path": "a.py", "edits": [{"oldText": "old = 1", "newText": "new = 2"}]}),
        ("git_diff", {"content": "@@ -1 +1 @@\n-old\n+new\n"}),
    )
    for background in (False, True, None):
        for tool_name, payload in payloads:
            preview = build_edit_preview(
                tool_name, payload, width=80, terminal_bg_is_light=background
            )
            assert preview is not None
            rendered = _render_truecolor(preview)
            assert "48;2;" not in rendered and "48;5;" not in rendered
            plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", rendered)
            assert "-" in plain and "+" in plain and "1" in plain


def test_preview_does_not_paint_its_own_background() -> None:
    """The preview must let the terminal background show through.

    Any RGB background escape would paint a rectangle in a colour picked
    without knowing the operator's scheme.
    """
    preview = build_edit_preview(
        "write_file",
        {"path": "a.py", "content": _PY_SNIPPET},
        width=80,
        terminal_bg_is_light=False,
    )
    rendered = _render_truecolor(preview)
    assert "48;2;" not in rendered, f"preview painted an RGB background:\n{rendered!r}"


def test_preview_colours_differ_between_light_and_dark_backgrounds() -> None:
    """The same content highlights differently on a light vs dark terminal.

    Dark backgrounds get the bright ANSI slots, light backgrounds the
    normal ones; identical output would mean the resolved background is
    not reaching the renderable.
    """
    payload = {"path": "a.py", "content": _PY_SNIPPET}
    on_dark = _render_truecolor(
        build_edit_preview("write_file", payload, width=80, terminal_bg_is_light=False)
    )
    on_light = _render_truecolor(
        build_edit_preview("write_file", payload, width=80, terminal_bg_is_light=True)
    )
    assert on_dark != on_light, (
        "light and dark backgrounds produced identical output; the resolved "
        "background is not reaching the Syntax renderable"
    )


def test_diff_markers_use_background_appropriate_styles() -> None:
    """The ``-`` / ``+`` marker colours are the background-aware variants."""
    payload = {
        "path": "a.py",
        "edits": [{"oldText": "x = 1\n", "newText": "x = 2\n"}],
    }
    on_dark = _render_truecolor(
        build_edit_preview("edit_file", payload, width=80, terminal_bg_is_light=False)
    )
    on_light = _render_truecolor(
        build_edit_preview("edit_file", payload, width=80, terminal_bg_is_light=True)
    )
    assert on_dark != on_light, "diff marker styles did not change with the terminal background"


def test_unknown_extension_still_gets_a_real_lexer() -> None:
    """Extensions outside the fast-path map resolve to a usable lexer alias.

    ``get_lexer_for_filename(...).name`` returns a DISPLAY name
    (``"Ruby"``), which ``Syntax`` cannot resolve -- every such file
    silently rendered unhighlighted. The alias (``"ruby"``) is what the
    lexer lookup accepts.
    """
    preview = build_edit_preview(
        "write_file",
        {"path": "lib/thing.rb", "content": "def go\n  puts 'hi'\nend\n"},
        width=80,
    )
    assert isinstance(preview, Syntax)
    assert preview.lexer is not None, "unknown-extension path resolved no lexer"
    assert preview.lexer.name == "Ruby", (
        f"expected the Ruby lexer via alias lookup, got {preview.lexer.name!r}"
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

    module_path = Path(__file__).parent.parent.parent / "ralph" / "display" / "edit_preview.py"
    assert module_path.exists(), f"module missing at {module_path}"
    text = module_path.read_text(encoding="utf-8")
    import re

    hex_pattern = re.compile(r"#[0-9A-Fa-f]{6}")
    hits = hex_pattern.findall(text)
    assert not hits, (
        f"edit_preview.py must not contain hex color literals "
        f"(Okabe-Ito discipline); found {hits!r}"
    )


def test_grep_and_search_result_previews_render_numbered_hits_and_emphasis() -> None:
    """S-1: result hits preserve each source lexer, line number, and match carrier."""
    grep = build_edit_preview(
        "grep_files",
        {
            "content": '{"matches":[{"path":"a.py","line":17,"text":"needle = 1"}]}',
            "pattern": "needle",
        },
        width=80,
    )
    assert isinstance(grep, Group)
    syntax = next(item for item in grep.renderables if isinstance(item, Syntax))
    assert syntax.lexer.name == "Python"
    assert syntax.start_line == 17
    assert syntax._stylized_ranges  # matched text has a named emphasis carrier
    assert _render_truecolor(grep) != _render_truecolor(
        build_edit_preview(
            "grep_files",
            {"content": '{"matches":[{"path":"a.py","line":17,"text":"needle = 1"}]}'},
            width=80,
        )
    )
    assert "\x1b[1;" in _render_truecolor(grep)
    rendered = io.StringIO()
    Console(file=rendered, force_terminal=False, color_system=None, width=80).print(grep)
    assert "a.py" in rendered.getvalue()
    assert "needle = 1" in rendered.getvalue()

    search = build_edit_preview(
        "search_files", {"content": '{"matches":["a.py","settings.yaml"]}'}, width=80
    )
    assert search is not None
    rendered = io.StringIO()
    Console(file=rendered, force_terminal=False, color_system=None, width=80).print(search)
    assert "a.py" in rendered.getvalue() and "settings.yaml" in rendered.getvalue()


def test_grep_record_projection_is_numbered_ansi_free_and_shared_budgeted() -> None:
    """S-1: records retain hit structure while a result cannot exceed the shared cap."""
    matches = [{"path": "a.py", "line": index, "text": f"needle = {index}"} for index in range(50)]
    record, _ = preview_record_text(
        "grep_files",
        {"input": {"content": json.dumps({"matches": matches}), "pattern": "needle"}},
        glyphs_enabled=False,
    )
    assert "\x1b" not in record
    assert "a.py:   0 needle = 0" in record
    assert "   1 needle = 1" in record
    assert "... (10 more lines)" in record


def test_preview_payload_parser_matrix_classifies_every_shipped_parser() -> None:
    """S-2: adding a parser requires an explicit mapped or declined classification."""
    parser_dir = Path(__file__).parents[2] / "ralph" / "agents" / "parsers"
    shipped = {
        path.stem
        for path in parser_dir.glob("*.py")
        if not path.stem.startswith("_")
        and path.stem
        not in {"base", "agent_output_line", "text_accumulator", "interactive_transcript_event"}
    }
    classified = {
        "claude",
        "codex",
        "cursor",
        "gemini",
        "opencode",
        "pi",
        "agy",
        "generic",
        "nanocoder",
        "claude_interactive",
        "claude_interactive_transcript_parser",
    }
    assert shipped == classified


def test_diff_preview_uses_real_hunk_line_numbers_or_marks_snippet_relative() -> None:
    """Diff metadata stays unnumbered while each hunk body keeps real file lines."""
    numbered = build_edit_preview(
        "git_show", {"content": "@@ -10,3 +20,3 @@\n context\n-old\n+new\n"}, width=80
    )
    preamble = build_edit_preview(
        "git_diff",
        {"content": "--- a/y.py\n+++ b/y.py\n@@ -5,1 +5,1 @@\n-a\n+b\n@@ -20,1 +30,1 @@\n-c\n+d\n"},
        width=80,
    )
    snippet = build_edit_preview("git_diff", {"content": "-old\n+new\n"}, width=80)
    assert numbered is not None and preamble is not None and snippet is not None
    numbered_text = io.StringIO()
    preamble_text = io.StringIO()
    snippet_text = io.StringIO()
    console = Console(file=numbered_text, force_terminal=False, color_system=None, width=80)
    console.print(numbered)
    Console(file=preamble_text, force_terminal=False, color_system=None, width=80).print(preamble)
    Console(file=snippet_text, force_terminal=False, color_system=None, width=80).print(snippet)
    assert re.search(r"\b20\s+context", numbered_text.getvalue())
    assert re.search(r"\b5\s+-a", preamble_text.getvalue())
    assert re.search(r"\b30\s+-c", preamble_text.getvalue())
    assert not re.search(r"^\s*\d+\s+(?:---|\+\+\+|@@)", preamble_text.getvalue())
    assert "(snippet)" in snippet_text.getvalue()
