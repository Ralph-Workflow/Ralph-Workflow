"""Parser coverage and bounded-preview contracts."""

from __future__ import annotations

import io
import json
from pathlib import Path

from rich.console import Console

from ralph.display.edit_preview import build_edit_preview
from ralph.display.preview_payload import payload_from_tool_event


def test_preview_payload_parser_coverage_matrix_uses_parser_emitted_shapes() -> None:
    """S-2: every parser is mapped from its emitted envelope or explicitly declines."""
    mapped: tuple[tuple[str, str, dict[str, object]], ...] = (
        ("claude", "Write", {"input": {"file_path": "a.py", "content": "x = 1"}}),
        ("codex", "apply_patch", {"input": '{"patch":"@@ -1 +1 @@\\n-old\\n+new\\n"}'}),
        (
            "cursor",
            "edit_file",
            {"args": {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}},
        ),
        ("gemini", "Write", {"args": "{'file_path': 'a.py', 'content': 'x = 1'}"}),
        ("opencode", "read", {"input": {"path": "a.py", "content": "x = 1"}}),
        (
            "pi",
            "NotebookEdit",
            {"args": {"path": "a.ipynb", "kernel": "python", "source": "x = 1"}},
        ),
    )
    for parser, tool_name, metadata in mapped:
        payload = payload_from_tool_event(tool_name, metadata)
        assert payload is not None, parser
        assert (
            build_edit_preview(tool_name, payload, width=80, terminal_bg_is_light=False) is not None
        ), parser

    declined = {
        "agy",
        "generic",
        "nanocoder",
        "claude_interactive",
        "claude_interactive_transcript_parser",
    }
    parser_dir = Path(__file__).parents[2] / "ralph" / "agents" / "parsers"
    shipped = {path.stem for path in parser_dir.glob("*.py") if not path.stem.startswith("_")} - {
        "base",
        "agent_output_line",
        "text_accumulator",
        "interactive_transcript_event",
    }
    assert shipped == {entry[0] for entry in mapped} | declined
    assert all(
        payload_from_tool_event("Write", {"raw_parser": parser}) is None for parser in declined
    )


def test_bounded_previews_use_ascii_elision_and_one_multi_file_budget() -> None:
    """S-3: ASCII terminals and multi-file reads retain bounded structure."""
    preview = build_edit_preview(
        "write_file",
        {"path": "a.py", "content": "\n".join(str(i) for i in range(41))},
        width=80,
        terminal_bg_is_light=False,
        glyphs_enabled=False,
    )
    output = io.StringIO()
    Console(file=output, force_terminal=False, color_system=None, width=80).print(preview)
    assert "... (1 more line)" in output.getvalue()
    assert "…" not in output.getvalue()

    body = "\n".join(f"line {index}" for index in range(40))
    payload = json.dumps(
        {"files": [{"path": f"file{index}.py", "content": body} for index in range(3)]}
    )
    multi = build_edit_preview(
        "read_multiple_files", {"content": payload}, width=80, terminal_bg_is_light=False
    )
    output = io.StringIO()
    Console(file=output, force_terminal=False, color_system=None, width=80).print(multi)
    assert output.getvalue().count("more lines") == 1
    assert len([line for line in output.getvalue().splitlines() if line.strip()]) <= 44
