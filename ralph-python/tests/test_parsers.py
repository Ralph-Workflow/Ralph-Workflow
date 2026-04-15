"""Unit tests for agent NDJSON parsers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.parsers import (
    ClaudeParser,
    CodexParser,
    GenericParser,
    OpenCodeParser,
    get_parser,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

EXPECTED_TEXT_RESULTS = 2


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

    assert len(results) == EXPECTED_TEXT_RESULTS
    assert results[0].type == "text"
    assert results[0].content == "Hello"
    assert results[1].type == "text"
    assert results[1].content == " World"


def test_claude_parser_message_stop() -> None:
    """Claude parser should suppress lifecycle-only events in user-facing output."""
    parser = ClaudeParser()
    lines = [
        '{"type":"message_start","message":{"id":"123"}}',
        '{"type":"content_block_start","content_block":{"type":"text"}}',
        '{"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}',
        '{"type":"content_block_stop"}',
        '{"type":"message_stop"}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "text"
    assert results[0].content == "Hello"


def test_claude_parser_error() -> None:
    """Claude parser should prefer the user-facing error message."""
    parser = ClaudeParser()
    lines = ['{"type":"error","error":{"type":"rate_limit_error","message":"Rate limited"}}']
    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "error"
    assert results[0].content == "Rate limited"


def test_claude_parser_invalid_json() -> None:
    """Test Claude parser handles invalid JSON gracefully."""
    parser = ClaudeParser()
    lines = ["not valid json", '{"type":"content_block_delta","delta":{"text":"Hello"}}']
    results = list(parser.parse(_make_lines(lines)))

    assert results[0].type == "raw"
    assert results[0].content == "not valid json"
    assert results[1].type == "text"


def test_claude_parser_stream_event_wrapper_for_ccs() -> None:
    """Claude/CCS stream_event wrapper should suppress wrapped lifecycle noise too."""
    parser = ClaudeParser()
    lines = [
        (
            '{"type":"stream_event","event":{"type":"content_block_delta",'
            '"delta":{"type":"text_delta","text":"Hello from stream"}}}'
        ),
        '{"type":"stream_event","event":{"type":"message_stop"}}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "text"
    assert results[0].content == "Hello from stream"


def test_claude_parser_assistant_message_content_blocks() -> None:
    """Claude assistant message should extract text blocks."""
    parser = ClaudeParser()
    lines = [
        '{"type":"assistant","message":{"content":[{"type":"text","text":"Final response"}]}}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "text"
    assert results[0].content == "Final response"


def test_claude_parser_result_event_does_not_emit_extra_stop_noise() -> None:
    """Claude result events should surface text without an extra lifecycle line."""
    parser = ClaudeParser()
    lines = [
        '{"type":"result","result":"Final answer"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "text"
    assert results[0].content == "Final answer"


def test_claude_parser_tool_result_keeps_structured_metadata_for_renderer_summary() -> None:
    """Claude tool results should keep structured metadata while surfacing readable text."""
    parser = ClaudeParser()
    lines = [
        (
            '{"type":"assistant","message":{"content":[{"type":"tool_result",'
            '"content":[{"type":"text","text":"file content"}],'
            '"tool_use_id":"toolu_123","name":"read"}]}}'
        ),
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "tool_result"
    assert results[0].content == "file content"
    assert results[0].metadata["name"] == "read"


def test_claude_parser_tool_use_block_is_emitted_as_user_visible_tool_activity() -> None:
    """Claude parser should surface tool-use blocks instead of raw block lifecycle events."""
    parser = ClaudeParser()
    lines = [
        (
            '{"type":"content_block_start","content_block":{"type":"tool_use",'
            '"name":"bash","input":{"command":"ls -la","workdir":"/tmp"}}}'
        ),
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "tool_use"
    assert results[0].content == "bash"
    assert results[0].metadata["input"] == {"command": "ls -la", "workdir": "/tmp"}


def test_opencode_parser_stream() -> None:
    """OpenCode parser should suppress lifecycle-only events in user-facing output."""
    parser = OpenCodeParser()
    lines = [
        '{"type":"step_start","id":"step-1"}',
        '{"type":"stream","content":"Hello"}',
        '{"type":"stream","content":" World"}',
        '{"type":"step_finish","id":"step-1"}',
        '{"type":"done"}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    assert results[0].type == "text"
    assert results[0].content == "Hello"
    assert results[1].type == "text"
    assert results[1].content == " World"
    assert len(results) == EXPECTED_TEXT_RESULTS


def test_opencode_parser_tool_use() -> None:
    """OpenCode parser should expose nested tool input for readable rendering."""
    parser = OpenCodeParser()
    lines = [
        '{"type":"tool_use","part":{"tool":"bash","input":{"command":"ls","workdir":"/repo"}}}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    assert results[0].type == "tool_use"
    assert results[0].content == "bash"
    assert results[0].metadata["input"] == {"command": "ls", "workdir": "/repo"}


def test_opencode_parser_text_event_with_part_payload() -> None:
    """OpenCode text event should parse text from nested part payload."""
    parser = OpenCodeParser()
    lines = [
        '{"type":"text","part":{"type":"text","text":"Nested OpenCode text"}}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "text"
    assert results[0].content == "Nested OpenCode text"


def test_opencode_parser_tool_use_completed_state_emits_result() -> None:
    """OpenCode completed tool state should emit tool_result."""
    parser = OpenCodeParser()
    lines = [
        (
            '{"type":"tool_use","part":{"tool":"read","state":'
            '{"status":"completed","output":"file content"}}}'
        ),
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "tool_result"
    assert results[0].content == "file content"
    assert results[0].metadata["tool"] == "read"


def test_opencode_parser_tool_result_preserves_tool_context_and_structured_metadata() -> None:
    """OpenCode tool results should preserve structured metadata for renderer summaries."""
    parser = OpenCodeParser()
    lines = [
        (
            '{"type":"tool_result","tool":"grep","result":{"matches":3,"path":"src"},'
            '"part":{"tool":"grep","input":{"pattern":"TODO"}}}'
        ),
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "tool_result"
    assert results[0].content == "{'matches': 3, 'path': 'src'}"
    assert results[0].metadata["tool"] == "grep"
    assert results[0].metadata["input"] == {"pattern": "TODO"}


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
    """Test get_parser returns CodexParser for 'codex'."""
    parser = get_parser("codex")
    assert isinstance(parser, CodexParser)


def test_codex_parser_dot_event_item_completed_agent_message() -> None:
    """Codex parser should handle dot-style item events from Rust reference."""
    parser = CodexParser()
    lines = [
        '{"type":"item.completed","item":{"type":"agent_message","text":"Codex says hi"}}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "text"
    assert results[0].content == "Codex says hi"


def test_codex_parser_text_delta_supports_delta_text_field() -> None:
    """Codex text_delta should parse delta.text in addition to delta.content."""
    parser = CodexParser()
    lines = [
        '{"type":"text_delta","delta":{"text":"Delta text field"}}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "text"
    assert results[0].content == "Delta text field"


def test_codex_parser_response_output_text_delta_is_parsed_as_text() -> None:
    """Codex response.output_text.delta should parse to a text event."""
    parser = CodexParser()
    lines = [
        '{"type":"response.output_text.delta","delta":"streamed thought"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "text"
    assert results[0].content == "streamed thought"


def test_codex_parser_accepts_sse_data_prefix_lines() -> None:
    """Codex parser should decode SSE-style lines prefixed with data:."""
    parser = CodexParser()
    lines = [
        'data: {"type":"text_delta","delta":"prefixed text"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "text"
    assert results[0].content == "prefixed text"


def test_codex_parser_item_completed_mcp_tool_result_maps_to_tool_result() -> None:
    """Codex parser should map completed MCP tool result items to tool_result."""
    parser = CodexParser()
    lines = [
        (
            '{"type":"item.completed","item":{'
            '"type":"mcp_tool_result",'
            '"tool":"write_memory",'
            '"result":{"status":"ok","written":2}'
            "}}"
        ),
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "tool_result"
    assert results[0].metadata["tool"] == "write_memory"
    assert results[0].metadata["result"] == {"status": "ok", "written": 2}


def test_get_parser_unknown_raises() -> None:
    """Test get_parser raises ValueError for unknown type."""
    with pytest.raises(ValueError, match="Unknown parser type"):
        get_parser("unknown_parser")
