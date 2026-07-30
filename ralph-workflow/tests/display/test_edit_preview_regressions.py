"""Regression coverage for bounded and escape-safe edit previews."""

from __future__ import annotations

import io
import json

from rich.console import Console

from ralph.display.edit_preview import build_edit_preview, preview_header, preview_record_text


def _render(preview: object) -> str:
    """Render a preview without terminal colour for observable assertions."""
    output = io.StringIO()
    Console(file=output, force_terminal=False, color_system=None, width=100).print(preview)
    return output.getvalue()


def test_edit_preview_regression_many_hunks_stay_bounded() -> None:
    """DA-001: many edits share one cap and one elision marker."""
    preview = build_edit_preview(
        "edit_file",
        {
            "path": "a.py",
            "edits": [
                {"oldText": f"old_{index} = 1", "newText": f"new_{index} = 2"}
                for index in range(200)
            ],
        },
        width=100,
        terminal_bg_is_light=False,
    )
    assert preview is not None
    output = _render(preview)
    assert len([line for line in output.splitlines() if line.strip()]) <= 45
    assert output.count("more lines") == 1


def test_edit_preview_regression_many_diff_hunks_stay_bounded() -> None:
    """DA-002: a multi-hunk diff shares the same cap and elision marker."""
    content = "\n".join(
        f"@@ -{index},10 +{index},10 @@\n" + "\n".join(f"+line_{line}" for line in range(10))
        for index in range(1, 31)
    )
    preview = build_edit_preview(
        "git_diff", {"content": content}, width=100, terminal_bg_is_light=False
    )
    assert preview is not None
    output = _render(preview)
    assert len([line for line in output.splitlines() if line.strip()]) <= 45
    assert output.count("more lines") == 1


def test_overflowed_preview_cites_the_raw_overflow_log() -> None:
    """DA-001: every preview projection cites its raw overflow source conditionally."""
    overflow_ref = ".agent/raw/unit-1.log"
    body = "\n".join(f"line {index}" for index in range(60))
    preview = build_edit_preview(
        "write_file",
        {"path": "a.py", "content": body},
        width=100,
        terminal_bg_is_light=False,
        overflow_ref=overflow_ref,
    )
    assert preview is not None
    rendered_preview = _render(preview)
    assert rendered_preview.count(f"[see {overflow_ref}]") == 1
    assert rendered_preview.count("more lines") == 1
    assert "469 B" in rendered_preview
    record, _ = preview_record_text(
        "write_file",
        {"input": {"path": "a.py", "content": body}},
        overflow_ref=overflow_ref,
        glyphs_enabled=True,
    )
    matches = [{"path": "a.py", "line": index, "text": f"line {index}"} for index in range(60)]
    grep_record, _ = preview_record_text(
        "grep_files",
        {"input": {"content": json.dumps({"matches": matches})}},
        overflow_ref=overflow_ref,
        glyphs_enabled=True,
    )
    for projection in (record, grep_record):
        assert f"[see {overflow_ref}]" in projection
        assert " B" in projection
        assert "\x1b" not in projection
    unlinked_preview = build_edit_preview(
        "write_file", {"path": "a.py", "content": body}, width=100, terminal_bg_is_light=False
    )
    assert unlinked_preview is not None
    unlinked_record, _ = preview_record_text(
        "write_file", {"input": {"path": "a.py", "content": body}}, glyphs_enabled=True
    )
    unlinked_grep_record, _ = preview_record_text(
        "grep_files", {"input": {"content": json.dumps({"matches": matches})}}, glyphs_enabled=True
    )
    for projection in (_render(unlinked_preview), unlinked_record, unlinked_grep_record):
        assert "[see " not in projection


def test_edit_preview_regression_sanitizes_hostile_labels() -> None:
    """DA-003: agent-provided paths and search strings cannot inject escapes."""
    hostile = "\x1b[2Jsrc/\x1b]0;pwned\x07a.py"
    preview = build_edit_preview(
        "search_files",
        {"content": json.dumps({"matches": [hostile, {"path": hostile, "line": 1, "text": "x"}]})},
        width=100,
        terminal_bg_is_light=False,
    )
    assert preview is not None
    output = _render(preview)
    record, _ = preview_record_text("read_file", {"path": hostile, "content": "x = 1"})
    assert "\x1b" not in output
    assert "\x1b" not in preview_header("read_file", hostile).plain
    assert "\x1b" not in record
