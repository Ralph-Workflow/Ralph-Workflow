"""Tests for ClaudeParser noise-suppression improvements."""

from __future__ import annotations

from ralph.agents.parsers.claude import ClaudeParser


def test_thinking_content_block_start_does_not_emit_error() -> None:
    """thinking block_start must not produce an 'unsupported content block type' error."""
    parser = ClaudeParser()
    lines = [
        '{"type":"message_start","message":{"id":"msg-think-1"}}',
        '{"type":"content_block_start","index":0,"content_block":{"type":"thinking"}}',
        '{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"hi"}}',
        '{"type":"content_block_stop","index":0}',
        '{"type":"message_stop"}',
    ]
    results = list(parser.parse(iter(lines)))
    errors = [r for r in results if r.type == "error"]
    assert errors == [], f"Unexpected error results: {errors}"
    thinking = [r for r in results if r.type == "thinking"]
    assert len(thinking) == 1
    assert thinking[0].content == "hi"


def test_claude_model_qualified_lifecycle_is_suppressed() -> None:
    """model-qualified lifecycle markers must be completely suppressed."""
    parser = ClaudeParser()
    lines = [
        "claude/sonnet: message_delta",
        "claude/sonnet: user",
        "claude/sonnet: system (status=requesting)",
        "claude/sonnet: thinking",
    ]
    results = list(parser.parse(iter(lines)))
    assert results == [], f"Expected empty list, got: {results}"


def test_claude_model_qualified_text_and_tool_are_parsed() -> None:
    """model-qualified text lines and tool lines must be parsed correctly."""
    parser = ClaudeParser()
    lines = [
        "claude/sonnet: hello world",
        "claude/sonnet tool: mcp__ralph__read_file (path=ralph-workflow/ralph/x.py)",
    ]
    results = list(parser.parse(iter(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "hello world"

    tool_results = [r for r in results if r.type == "tool_use"]
    assert len(tool_results) == 1
    assert tool_results[0].content == "mcp__ralph__read_file"
    assert tool_results[0].metadata is not None
    input_obj = tool_results[0].metadata.get("input")
    assert isinstance(input_obj, dict)
    args = input_obj.get("args", "")
    assert "path=ralph-workflow/ralph/x.py" in str(args)


def test_bare_claude_lifecycle_is_suppressed() -> None:
    """Bare (non-model-qualified) lifecycle markers must also be suppressed."""
    parser = ClaudeParser()
    lines = [
        "claude: message_delta",
        "claude: user",
        "claude: thinking",
    ]
    results = list(parser.parse(iter(lines)))
    assert results == [], f"Expected empty list for bare lifecycle, got: {results}"


def test_claude_error_prefix_emits_error() -> None:
    """claude/<model> ✗: error text must surface as type='error'."""
    parser = ClaudeParser()
    lines = ["claude/sonnet ✗: unsupported content block type 'thinking' in agent output"]
    results = list(parser.parse(iter(lines)))
    assert len(results) == 1
    assert results[0].type == "error"
    assert "unsupported" in results[0].content
