"""Unit tests for agent NDJSON parsers."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from ralph.agents.parsers import ClaudeParser, GenericParser, OpenCodeParser, get_parser


def _make_lines(data: list[str]) -> Iterator[str]:
    """Helper to create line iterator from list."""
    return iter(data)


def test_claude_parser_content_block_delta() -> None:
    """Test Claude parser handles content_block_delta events."""
    parser = ClaudeParser()
    lines = [
        '{"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}',
        '{"type":"content_block_delta","delta":{"type":"text_delta","text":" World"}}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 2
    assert results[0].type == "text"
    assert results[0].content == "Hello"
    assert results[1].type == "text"
    assert results[1].content == " World"


def test_claude_parser_message_stop() -> None:
    """Test Claude parser handles message_stop events."""
    parser = ClaudeParser()
    lines = [
        '{"type":"message_start","message":{"id":"123"}}',
        '{"type":"content_block_start","content_block":{"type":"text"}}',
        '{"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}',
        '{"type":"message_stop"}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    types = [r.type for r in results]
    assert "message_start" in types
    assert "text" in types
    assert "stop" in types


def test_claude_parser_error() -> None:
    """Test Claude parser handles error events."""
    parser = ClaudeParser()
    lines = ['{"type":"error","error":{"type":"rate_limit_error","message":"Rate limited"}}']
    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "error"
    assert "rate_limit" in results[0].content


def test_claude_parser_invalid_json() -> None:
    """Test Claude parser handles invalid JSON gracefully."""
    parser = ClaudeParser()
    lines = ["not valid json", '{"type":"content_block_delta","delta":{"text":"Hello"}}']
    results = list(parser.parse(_make_lines(lines)))

    assert results[0].type == "raw"
    assert results[0].content == "not valid json"
    assert results[1].type == "text"


def test_opencode_parser_stream() -> None:
    """Test OpenCode parser handles stream events."""
    parser = OpenCodeParser()
    lines = [
        '{"type":"stream","content":"Hello"}',
        '{"type":"stream","content":" World"}',
        '{"type":"done"}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    assert results[0].type == "text"
    assert results[0].content == "Hello"
    assert results[1].type == "text"
    assert results[1].content == " World"
    assert results[2].type == "stop"


def test_opencode_parser_tool_use() -> None:
    """Test OpenCode parser handles tool_use events."""
    parser = OpenCodeParser()
    lines = ['{"type":"tool_use","tool":"bash","input":{"command":"ls"}}']
    results = list(parser.parse(_make_lines(lines)))

    assert results[0].type == "tool_use"
    assert results[0].content == "bash"


def test_generic_parser_content_fields() -> None:
    """Test GenericParser extracts content from common fields."""
    parser = GenericParser()
    lines = [
        '{"content":"Hello World"}',
        '{"text":"Another message"}',
        '{"message":"Third message"}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    assert results[0].content == "Hello World"
    assert results[1].content == "Another message"
    assert results[2].content == "Third message"


def test_generic_parser_error_detection() -> None:
    """Test GenericParser detects error objects."""
    parser = GenericParser()
    lines = [
        '{"type":"error","error":"Something went wrong"}',
        '{"error":{"message":"Another error"}}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    assert results[0].type == "error"
    assert "Something went wrong" in results[0].content
    assert results[1].type == "error"


def test_generic_parser_stop_markers() -> None:
    """Test GenericParser recognizes stop markers."""
    parser = GenericParser()
    lines = [
        '{"type":"stop"}',
        '{"type":"done"}',
        '{"type":"complete"}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    assert all(r.type == "stop" for r in results)


def test_get_parser_claude() -> None:
    """Test get_parser returns ClaudeParser for 'claude'."""
    parser = get_parser("claude")
    assert isinstance(parser, ClaudeParser)


def test_get_parser_opencode() -> None:
    """Test get_parser returns OpenCodeParser for 'opencode'."""
    parser = get_parser("opencode")
    assert isinstance(parser, OpenCodeParser)


def test_get_parser_generic() -> None:
    """Test get_parser returns GenericParser for 'generic'."""
    parser = get_parser("generic")
    assert isinstance(parser, GenericParser)


def test_get_parser_codex() -> None:
    """Test get_parser returns GenericParser for 'codex'."""
    parser = get_parser("codex")
    assert isinstance(parser, GenericParser)


def test_get_parser_unknown_raises() -> None:
    """Test get_parser raises ValueError for unknown type."""
    with pytest.raises(ValueError, match="Unknown parser type"):
        get_parser("unknown_parser")
