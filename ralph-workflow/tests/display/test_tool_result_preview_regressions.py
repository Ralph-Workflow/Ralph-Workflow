"""Live tool-result preview regressions."""

from __future__ import annotations

import io
import re
import string

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from rich.console import Console

from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.context import make_display_context
from ralph.display.edit_preview import build_edit_preview, preview_record_text
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.preview_payload import payload_from_tool_event


def _make_truecolor_display() -> tuple[ParallelDisplay, io.StringIO]:
    buffer = io.StringIO()
    console = Console(
        file=buffer,
        force_terminal=True,
        color_system="truecolor",
        width=120,
        highlight=False,
    )
    return ParallelDisplay(make_display_context(console=console, env={})), buffer


def test_unpreviewable_successful_results_keep_their_inline_body() -> None:
    """DA-002: result content is suppressed only when a preview prints."""
    for tool_name, body in (
        ("grep_files", '{"matches": [], "truncated": false}'),
        ("search_files", "SENTINEL_LOST_BODY: 0 matches for the query"),
        ("read_multiple_files", "SENTINEL_LOST_BODY: unavailable"),
    ):
        display, buffer = _make_truecolor_display()
        display.emit_parsed_event(
            unit_id="dev-1",
            kind=ActivityEventKind.TOOL_RESULT,
            content=body,
            metadata={"tool_name": tool_name, "exit_code": 0},
        )
        display.stop()
        plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", buffer.getvalue())
        assert plain.count(body) == 1


def test_preview_payload_rejects_oversized_string_envelopes() -> None:
    """Hostile parser strings decline safely instead of exhausting literal parsing."""
    hostile = "-" * 10_000
    assert payload_from_tool_event("Write", {"input": hostile}) is None
    assert payload_from_tool_event("Write", {"args": hostile}) is None


def test_preview_payload_accepts_bounded_large_json_envelopes() -> None:
    """Valid parser JSON below the safety cap remains previewable."""
    content = "x" * 8_000
    payload = payload_from_tool_event(
        "Write", {"input": f'{{"path":"a.py","content":"{content}"}}'}
    )
    assert payload is not None
    assert payload.content == content


@pytest.mark.timeout_seconds(5)
@settings(max_examples=10, deadline=None)
@given(
    tool=st.sampled_from(("write_file", "edit_file", "Write", "Edit")),
    body=st.text(alphabet=string.printable, max_size=2_048),
    path=st.one_of(st.none(), st.text(alphabet=string.printable, max_size=128)),
)
def test_preview_builders_never_raise_for_arbitrary_parser_payloads(
    tool: str, body: str, path: str | None
) -> None:
    """Recognized parser envelopes never escape preview construction.

    5s timeout: Hypothesis exploration plus preview construction can exceed
    the default 1s wall-clock budget under parallel-suite load.
    """
    payloads: tuple[dict[str, object], ...] = (
        {"path": path, "content": body},
        {"path": path, "edits": [{"oldText": body, "newText": body}]},
        {"input": body},
        {"args": body},
    )
    for terminal_bg_is_light in (True, False, None):
        for payload in payloads:
            build_edit_preview(tool, payload, width=80, terminal_bg_is_light=terminal_bg_is_light)
            preview_record_text(tool, payload)


def test_plain_text_read_file_result_renders_a_highlighted_preview() -> None:
    """A correlated full read previews its documented plain-text response."""
    display, buffer = _make_truecolor_display()
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_USE,
        content="mcp__ralph__read_file",
        metadata={"input": {"path": "a.py", "line_start": 200}},
    )
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_RESULT,
        content="def render():\n    return 1\n",
        metadata={"exit_code": 0},
    )
    display.stop()
    output = buffer.getvalue()
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    assert "\x1b[38;2;" in output
    assert "read  a.py" in plain
    assert re.search(r"\b200\s+def render", plain)


def test_read_result_byte_offset_is_marked_as_snippet_not_line_number() -> None:
    """DA-003: a byte offset never masquerades as a file line number."""
    display, buffer = _make_truecolor_display()
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_USE,
        content="mcp__ralph__read_file",
        metadata={"input": {"path": "a.py", "offset": 4096, "limit": 256}},
    )
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_RESULT,
        content="def render():\n    return 1\n",
        metadata={"exit_code": 0},
    )
    display.stop()
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", buffer.getvalue())
    assert "(snippet)" in plain
    assert not re.search(r"\b4096\s+def render", plain)
    assert re.search(r"\b1\s+def render", plain)


def test_read_result_tail_window_is_marked_as_snippet() -> None:
    """DA-003: tail windows lack source lines and stay visibly relative."""
    display, buffer = _make_truecolor_display()
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_USE,
        content="mcp__ralph__read_file",
        metadata={"input": {"path": "a.py", "tail": 2}},
    )
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_RESULT,
        content="def render():\n    return 1\n",
        metadata={"exit_code": 0},
    )
    display.stop()
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", buffer.getvalue())
    assert "(snippet)" in plain
    assert re.search(r"\b1\s+def render", plain)


def test_read_result_head_window_is_marked_as_snippet() -> None:
    """DA-003: head windows lack source lines and stay visibly relative."""
    display, buffer = _make_truecolor_display()
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_USE,
        content="mcp__ralph__read_file",
        metadata={"input": {"path": "a.py", "head": 2}},
    )
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_RESULT,
        content="def render():\n    return 1\n",
        metadata={"exit_code": 0},
    )
    display.stop()
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", buffer.getvalue())
    assert "(snippet)" in plain
    assert re.search(r"\b1\s+def render", plain)


def test_tool_result_regression_preserves_traceback_lines_in_live_log() -> None:
    """DA-003: an unpreviewable multiline result keeps source line boundaries."""
    display, buffer = _make_truecolor_display()
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "x.py", line 3\n'
        '    raise ValueError("boom")\n'
        "ValueError: boom"
    )
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_RESULT,
        content=traceback,
        metadata={"tool_name": "Bash", "exit_code": 1},
    )
    display.stop()
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", buffer.getvalue())
    assert any(line.endswith("ValueError: boom") for line in plain.splitlines())
    assert any('File "x.py", line 3' in line for line in plain.splitlines())


def test_git_log_result_correlates_to_originating_tool_use_and_previews_once() -> None:
    """S-1: the seventh read tool inherits its call identity before rendering."""
    display, buffer = _make_truecolor_display()
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_USE,
        content="mcp__ralph__git_log",
        metadata={"input": {"count": 1}, "tool_call_id": "git-log-1"},
    )
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_RESULT,
        content="abc1234 Add preview coverage\n",
        metadata={"exit_code": 0, "tool_call_id": "git-log-1"},
    )
    display.stop()
    output = buffer.getvalue()
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    assert "git_log" in plain
    assert plain.count("abc1234 Add preview coverage") == 1


def test_git_log_preview_preserves_each_commit_line_as_plain_text() -> None:
    """DA-002: git_log results retain commit boundaries instead of markdown reflow."""
    display, buffer = _make_truecolor_display()
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_USE,
        content="mcp__ralph__git_log",
        metadata={"input": {"count": 3}, "tool_call_id": "git-log-lines"},
    )
    commits = "abc1234 First commit\ndef5678 Second commit\n9876abc # literal subject\n"
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_RESULT,
        content=commits,
        metadata={"exit_code": 0, "tool_call_id": "git-log-lines"},
    )
    display.stop()
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", buffer.getvalue())
    assert all(
        any(commit in line for line in plain.splitlines())
        for commit in commits.splitlines()
        if commit
    )


def test_no_color_result_preview_keeps_structure_without_ansi() -> None:
    """DA-003: NO_COLOR disables preview color without dropping file content."""
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=True, color_system="truecolor", width=100)
    display = ParallelDisplay(make_display_context(console=console, env={"NO_COLOR": "1"}))
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_USE,
        content="mcp__ralph__read_file",
        metadata={"input": {"path": "a.py"}, "tool_call_id": "no-color-read"},
    )
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_RESULT,
        content="def render():\n    return 1\n",
        metadata={"exit_code": 0, "tool_call_id": "no-color-read"},
    )
    display.stop()
    output = buffer.getvalue()
    assert "\x1b[38;2;" not in output
    plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    assert "a.py" in plain and "def render" in plain and "1" in plain


def test_markdown_handoff_rules_use_explicit_style() -> None:
    """DA-006: markdown handoff delimiters avoid Rich's ANSI-slot default."""
    display, buffer = _make_truecolor_display()
    display._render_text_block("Handoff", "# Heading", "development")
    output = buffer.getvalue()
    assert "\x1b[92m" not in output
    assert not re.search(r"\x1b\[(?:[0-9;]*;)?(?:3[0-7]|9[0-7]|4[0-7]|10[0-7])m", output)


def test_nested_grep_pattern_reaches_result_emphasis() -> None:
    """DA-003: production-shaped nested tool input keeps match emphasis."""
    display, buffer = _make_truecolor_display()
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_USE,
        content="mcp__ralph__grep_files",
        metadata={"input": {"path": "src", "pattern": "needle_token"}},
    )
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_RESULT,
        content='{"matches": [{"path": "src/a.py", "line": 12, "text": "x = needle_token()"}]}',
        metadata={"tool_name": "mcp__ralph__grep_files", "exit_code": 0},
    )
    display.stop()
    output = buffer.getvalue()
    assert "\x1b[1;" in output
    assert "needle_token" in output
