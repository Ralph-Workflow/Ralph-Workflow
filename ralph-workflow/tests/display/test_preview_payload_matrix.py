"""Parser coverage and bounded-preview contracts."""

from __future__ import annotations

import io
import json
from pathlib import Path

from rich.console import Console
from rich.syntax import Syntax

from ralph.agents.parsers.agy import AgyParser
from ralph.agents.parsers.claude_interactive import ClaudeInteractiveParser
from ralph.agents.parsers.cursor import CursorParser
from ralph.agents.parsers.generic import GenericParser
from ralph.agents.parsers.nanocoder import NanocoderParser
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
        (
            "cursor",
            "edit_file",
            {"input": {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}},
        ),
        ("gemini", "Write", {"args": "{'file_path': 'a.py', 'content': 'x = 1'}"}),
        ("opencode", "read", {"input": {"path": "a.py", "content": "x = 1"}}),
        (
            "pi",
            "NotebookEdit",
            {"args": {"path": "a.ipynb", "kernel": "python", "source": "x = 1"}},
        ),
        (
            "claude_interactive",
            "Write",
            {"tool": "Write", "input": {"file_path": "a.py", "content": "x = 1"}},
        ),
        (
            "claude_interactive_transcript_parser",
            "Write",
            {"tool": "Write", "input": {"file_path": "a.py", "content": "x = 1"}},
        ),
    )
    for parser, tool_name, metadata in mapped:
        payload = payload_from_tool_event(tool_name, metadata)
        assert payload is not None, parser
        assert (
            build_edit_preview(tool_name, payload, width=80, terminal_bg_is_light=False) is not None
        ), parser

    interactive_event = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_write",
                        "name": "Write",
                        "input": {"file_path": "a.py", "content": "x = 1"},
                    }
                ]
            },
        }
    )
    interactive = next(iter(ClaudeInteractiveParser().parse(iter([interactive_event]))))
    assert interactive.type == "tool_use"
    interactive_payload = payload_from_tool_event(interactive.content, interactive.metadata)
    assert interactive_payload is not None
    assert (
        build_edit_preview(
            interactive.content, interactive_payload, width=80, terminal_bg_is_light=False
        )
        is not None
    )

    declined = {"agy", "generic", "nanocoder"}
    parser_dir = Path(__file__).parents[2] / "ralph" / "agents" / "parsers"
    shipped = {path.stem for path in parser_dir.glob("*.py") if not path.stem.startswith("_")} - {
        "base",
        "agent_output_line",
        "text_accumulator",
        "interactive_transcript_event",
    }
    assert shipped == {entry[0] for entry in mapped} | declined

    nanocoder = next(iter(NanocoderParser().parse(iter(["⚒ Executed nanocoder_tool"]))))
    assert nanocoder.type == "tool_use"
    assert payload_from_tool_event(nanocoder.content, nanocoder.metadata) is None

    generic = next(iter(GenericParser().parse(iter(["[plain] tool: generic_tool"]))))
    assert generic.type == "tool_use"
    assert payload_from_tool_event(generic.content, generic.metadata) is None

    agy = next(iter(AgyParser().parse(iter(["[plain] tool: agy_tool"]))))
    assert agy.type == "text"


def test_cursor_nested_args_envelope_reaches_preview_payload() -> None:
    """S-1: Cursor's live nested args are flattened before preview normalization."""
    event = {
        "type": "tool_call",
        "subtype": "started",
        "tool_call": {
            "edit_file": {"args": {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}}
        },
    }
    line = next(iter(CursorParser().parse(iter([json.dumps(event)]))))
    payload = payload_from_tool_event(line.content, line.metadata)
    assert payload is not None and payload.path == "a.py" and payload.operation == "replace"
    assert (
        build_edit_preview(line.content, payload, width=80, terminal_bg_is_light=False) is not None
    )


def test_notebook_edit_uses_the_kernel_lexer_despite_notebook_path() -> None:
    """DA-001: Notebook cell code follows its declared kernel, not .ipynb guessing."""
    for kernel, source, lexer_name in (
        ("python", "1\n", "PythonLexer"),
        ("julia", "f(x) = x + 1\n", "JuliaLexer"),
    ):
        payload = payload_from_tool_event(
            "NotebookEdit",
            {"input": {"notebook_path": "analysis.ipynb", "kernel": kernel, "new_source": source}},
        )
        assert payload is not None and payload.path == "analysis.ipynb"
        preview = build_edit_preview("NotebookEdit", payload, width=80, terminal_bg_is_light=False)
        assert isinstance(preview, Syntax)
        assert type(preview.lexer).__name__ == lexer_name


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
    rendered = output.getvalue()
    assert "... (1 more line · 112 B)" in rendered
    assert "…" not in rendered

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
