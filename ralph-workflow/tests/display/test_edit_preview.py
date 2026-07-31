"""Black-box regression tests for edit-preview rendering."""

from __future__ import annotations

import io
import re

from rich.console import Console
from rich.markdown import Markdown
from rich.syntax import Syntax

from ralph.display.edit_preview import build_edit_preview, render_markdown_preview
from ralph.display.theme import diff_fill_styles


def _render_truecolor(preview: object, *, width: int = 80) -> str:
    """Render a preview while retaining terminal styles for assertions."""
    output = io.StringIO()
    Console(file=output, force_terminal=True, color_system="truecolor", no_color=False, width=width).print(preview)
    return output.getvalue()


def _plain(rendered: str) -> str:
    """Remove ANSI control sequences from a rendered preview."""
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", rendered)


def test_build_edit_preview_write_file_uses_path_lexer() -> None:
    """A Python write is rendered with the Python lexer and line gutters."""
    preview = build_edit_preview("write_file", {"path": "src/example.py", "content": "x = 1\ny = 2\n"}, width=80, terminal_bg_is_light=False)
    assert isinstance(preview, Syntax)
    assert preview.lexer.name == "Python"
    assert "x = 1" in _plain(_render_truecolor(preview))


def test_build_edit_preview_artifact_uses_markdown_renderer() -> None:
    """Pathless artifact content uses the shared Markdown renderer."""
    preview = build_edit_preview("ralph_stage_md_artifact", {"artifact_type": "plan", "content": "# Heading\n\nBody."}, width=80, terminal_bg_is_light=False)
    assert isinstance(preview, Markdown)


def test_markdown_preview_regression_wraps_prose_without_wrapping_fenced_code() -> None:
    """S-1: prose wraps while a fenced source line remains a single row."""
    rendered = _plain(_render_truecolor(render_markdown_preview(f"{'prose ' * 20}\n\n```python\n{'identifier_' * 10}\n```", width=80, terminal_bg_is_light=False)))
    rows = rendered.splitlines()
    assert sum("prose" in row for row in rows) > 1
    assert sum("identifier_" in row for row in rows) == 1


def test_diff_preview_regression_is_transparent_by_default_and_paints_when_opted_in() -> None:
    """S-3: fills are explicit and retain marker/gutter structure and polarity."""
    payloads = (
        ("edit_file", {"path": "a.py", "edits": [{"oldText": "old = 1", "newText": "new = 2"}]}),
        ("git_diff", {"content": "@@ -1 +1 @@\n-old\n+new\n"}),
    )
    for terminal_bg_is_light in (False, True, None):
        for tool_name, payload in payloads:
            preview = build_edit_preview(tool_name, payload, width=80, terminal_bg_is_light=terminal_bg_is_light)
            assert preview is not None
            rendered = _render_truecolor(preview)
            assert "48;2;" not in rendered and "48;5;" not in rendered
            fills = diff_fill_styles(terminal_bg_is_light)
            painted = build_edit_preview(
                tool_name,
                payload,
                width=80,
                terminal_bg_is_light=terminal_bg_is_light,
                diff_fills=fills,
            )
            assert painted is not None
            painted_rendered = _render_truecolor(painted)
            if fills is None:
                assert "48;2;" not in painted_rendered and "48;5;" not in painted_rendered
            else:
                assert all(
                    f"48;2;{int(fill[1:3], 16)};{int(fill[3:5], 16)};{int(fill[5:7], 16)}" in painted_rendered
                    for fill in fills
                )
            plain = _plain(painted_rendered)
            assert "-" in plain and "+" in plain and "1" in plain
            if tool_name == "git_diff":
                assert "@@ -1 +1 @@" in plain


def test_edit_preview_keeps_old_and_new_hunks_and_gutters_without_colour() -> None:
    """The structural diff remains complete when styles are stripped."""
    preview = build_edit_preview("edit_file", {"path": "a.py", "edits": [{"oldText": "old = 1\n", "newText": "new = 2\n", "start_line": 7}]}, width=80, terminal_bg_is_light=False)
    assert preview is not None
    rendered = _plain(_render_truecolor(preview))
    assert "-" in rendered and "+" in rendered
    assert "old = 1" in rendered and "new = 2" in rendered and "7" in rendered


def test_build_edit_preview_unknown_tool_is_ignored() -> None:
    """Unrecognized tools preserve the header-only behavior."""
    assert build_edit_preview("unknown_tool", {"path": "a.py", "content": "x = 1"}, width=80, terminal_bg_is_light=False) is None


def test_build_edit_preview_long_content_reports_elision() -> None:
    """Bounded previews retain an explicit omission marker."""
    preview = build_edit_preview("write_file", {"path": "a.py", "content": "\n".join(f"line {number}" for number in range(41))}, width=80, terminal_bg_is_light=False)
    assert preview is not None
    assert "more line" in _plain(_render_truecolor(preview)).lower()
