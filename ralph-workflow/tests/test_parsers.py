"""Unit tests for agent NDJSON parsers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.parsers import (
    ClaudeParser,
    CodexParser,
    GeminiParser,
    GenericParser,
    OpenCodeParser,
    get_parser,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

EXPECTED_TWO_LINES = 2


def _make_lines(data: list[str]) -> Iterator[str]:
    """Helper to create line iterator from list."""
    return iter(data)


def test_claude_parser_content_block_delta() -> None:
    """Claude parser should accumulate orphan content_block_delta text coherently."""
    parser = ClaudeParser()
    lines = [
        '{"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}',
        '{"type":"content_block_delta","delta":{"type":"text_delta","text":" World"}}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    text_results = [result for result in results if result.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Hello World"


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


def test_claude_parser_prefixed_transcript_user_message_extracts_tool_result() -> None:
    """Claude transcript lines with a message= payload should extract embedded tool results."""
    parser = ClaudeParser()
    lines = [
        (
            'claude user: message={"content":[{"content":"Task: produce a spec-compliant '
            'MCP commit_message artifact","tool_use_id":"toolu_123","type":"tool_result"}],'
            '"role":"user"}'
        ),
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "tool_result"
    assert results[0].content == "Task: produce a spec-compliant MCP commit_message artifact"
    assert results[0].metadata["tool_use_id"] == "toolu_123"


def test_claude_parser_prefixed_transcript_message_delta_is_suppressed() -> None:
    """Claude transcript message_delta lines should be treated as transport noise."""
    parser = ClaudeParser()
    lines = [
        (
            'claude message_delta: type=message_delta; delta={"stop_reason":"tool_use"}; '
            'usage={"output_tokens":170}; context_management={"applied_edits":[]}'
        ),
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert results == []


def test_opencode_parser_stream() -> None:
    """OpenCode parser should accumulate stream deltas and emit at step_finish."""
    parser = OpenCodeParser()
    lines = [
        '{"type":"step_start","id":"step-1"}',
        '{"type":"stream","content":"Hello"}',
        '{"type":"stream","content":" World"}',
        '{"type":"step_finish","id":"step-1"}',
        '{"type":"done"}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    # With delta accumulation, Hello and World are merged into Hello World
    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Hello World"


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
    """GenericParser should extract common text fields and flush them on stop markers."""
    parser = GenericParser()
    lines = [
        '{"content":"Hello World"}',
        '{"type":"done"}',
        '{"text":"Another message"}',
        '{"type":"done"}',
        '{"message":"Third message"}',
        '{"type":"done"}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    text_results = [result for result in results if result.type == "text"]
    assert text_results[0].content == "Hello World"
    assert text_results[1].content == "Another message"
    assert text_results[2].content == "Third message"


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


def test_codex_parser_delta_accumulates_to_single_line_on_stop() -> None:
    """Codex parser should accumulate multiple deltas into one text line on stop."""
    parser = CodexParser()
    lines = [
        '{"type":"text_delta","delta":"Hello","response_id":"resp-1"}',
        '{"type":"text_delta","delta":" ","response_id":"resp-1"}',
        '{"type":"text_delta","delta":"World","response_id":"resp-1"}',
        '{"type":"text_delta","delta":"!","response_id":"resp-1"}',
        '{"type":"response.completed","response_id":"resp-1"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    # Should be 2 results: one coalesced text line + stop
    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Hello World!"


def test_codex_parser_paragraph_boundary_yields_two_lines() -> None:
    """Codex parser should flush on paragraph boundary (double newline)."""
    parser = CodexParser()
    lines = [
        '{"type":"text_delta","delta":"First paragraph","response_id":"resp-1"}',
        '{"type":"text_delta","delta":"\n\n","response_id":"resp-1"}',
        '{"type":"text_delta","delta":"Second paragraph","response_id":"resp-1"}',
        '{"type":"response.completed","response_id":"resp-1"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == EXPECTED_TWO_LINES
    assert text_results[0].content == "First paragraph"
    assert text_results[1].content == "Second paragraph"


def test_codex_parser_error_delta_flushes_immediately() -> None:
    """Codex parser should flush pending text before emitting error."""
    parser = CodexParser()
    lines = [
        '{"type":"text_delta","delta":"Some text","response_id":"resp-1"}',
        '{"type":"error","error":{"message":"Rate limited"}}',
        '{"type":"response.completed","response_id":"resp-1"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    # Should have text + error + stop
    text_results = [r for r in results if r.type == "text"]
    error_results = [r for r in results if r.type == "error"]
    assert len(text_results) == 1
    assert text_results[0].content == "Some text"
    assert len(error_results) == 1
    assert error_results[0].content == "Rate limited"


def test_codex_parser_iterator_exhaustion_flushes_accumulator() -> None:
    """Codex parser should flush remaining accumulator when iterator ends."""
    parser = CodexParser()
    lines = [
        '{"type":"text_delta","delta":"Partial","response_id":"resp-1"}',
        '{"type":"text_delta","delta":" text","response_id":"resp-1"}',
        # No explicit stop event
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Partial text"


def test_opencode_parser_delta_accumulates_to_single_line_on_done() -> None:
    """OpenCode parser should accumulate multiple stream deltas into one text line on done."""
    parser = OpenCodeParser()
    lines = [
        '{"type":"step_start","id":"step-1"}',
        '{"type":"stream","content":"Hello"}',
        '{"type":"stream","content":" "}',
        '{"type":"stream","content":"World"}',
        '{"type":"step_finish","id":"step-1"}',
        '{"type":"done"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Hello World"


def test_opencode_parser_paragraph_boundary_yields_two_lines() -> None:
    """OpenCode parser should flush on paragraph boundary (double newline)."""
    parser = OpenCodeParser()
    lines = [
        '{"type":"step_start","id":"step-1"}',
        '{"type":"stream","content":"Para 1\n\n"}',
        '{"type":"stream","content":"Para 2"}',
        '{"type":"step_finish","id":"step-1"}',
        '{"type":"done"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == EXPECTED_TWO_LINES
    assert text_results[0].content == "Para 1"
    assert text_results[1].content == "Para 2"


def test_opencode_parser_error_flushes_immediately() -> None:
    """OpenCode parser should flush pending text before emitting error."""
    parser = OpenCodeParser()
    lines = [
        '{"type":"step_start","id":"step-1"}',
        '{"type":"stream","content":"Some text"}',
        '{"type":"error","error":{"message":"Tool failed"}}',
        '{"type":"step_finish","id":"step-1"}',
        '{"type":"done"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    error_results = [r for r in results if r.type == "error"]
    assert len(text_results) == 1
    assert text_results[0].content == "Some text"
    assert len(error_results) == 1
    assert error_results[0].content == "Tool failed"


def test_opencode_parser_iterator_exhaustion_flushes_accumulator() -> None:
    """OpenCode parser should flush remaining accumulator when iterator ends."""
    parser = OpenCodeParser()
    lines = [
        '{"type":"step_start","id":"step-1"}',
        '{"type":"stream","content":"Partial"}',
        '{"type":"stream","content":" text"}',
        # No step_finish or done
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Partial text"


def test_gemini_parser_text_content_accumulates() -> None:
    """Gemini parser should accumulate text content into coherent blocks."""
    parser = GeminiParser()
    lines = [
        'data: {"type":"text","content":"Hello"}',
        'data: {"type":"text","content":" World"}',
        'data: {"type":"done"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Hello World"


def test_gemini_parser_paragraph_boundary_yields_two_lines() -> None:
    """Gemini parser should flush on paragraph boundary (double newline)."""
    parser = GeminiParser()
    lines = [
        'data: {"type":"text","content":"Para 1\n\n"}',
        'data: {"type":"text","content":"Para 2"}',
        'data: {"type":"done"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == EXPECTED_TWO_LINES
    assert text_results[0].content == "Para 1"
    assert text_results[1].content == "Para 2"


def test_gemini_parser_block_content_accumulates() -> None:
    """Gemini parser should accumulate block content."""
    parser = GeminiParser()
    lines = [
        'data: {"type":"block","content":"Block 1"}',
        'data: {"type":"block","content":" Block 2"}',
        'data: {"type":"stop"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Block 1 Block 2"


def test_gemini_parser_message_end_flushes_accumulator() -> None:
    """Gemini parser should flush on message_end."""
    parser = GeminiParser()
    lines = [
        'data: {"type":"text","content":"Final text"}',
        'data: {"type":"message_end"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Final text"


def test_gemini_parser_iterator_exhaustion_flushes_accumulator() -> None:
    """Gemini parser should flush remaining accumulator when iterator ends."""
    parser = GeminiParser()
    lines = [
        'data: {"type":"text","content":"Partial"}',
        'data: {"type":"text","content":" text"}',
        # No explicit stop
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Partial text"


def test_gemini_parser_tool_call_emitted_separately() -> None:
    """Gemini parser should emit tool_use events separately from text."""
    parser = GeminiParser()
    lines = [
        'data: {"type":"text","content":"Thinking..."}',
        'data: {"type":"tool_call","name":"bash","args":{"command":"ls"}}',
        'data: {"type":"done"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    tool_results = [r for r in results if r.type == "tool_use"]
    assert len(text_results) == 1
    assert text_results[0].content == "Thinking..."
    assert len(tool_results) == 1
    assert tool_results[0].content == "bash"


def test_get_parser_gemini() -> None:
    """Test get_parser returns GeminiParser for 'gemini'."""
    parser = get_parser("gemini")
    assert isinstance(parser, GeminiParser)


def test_get_parser_unknown_raises() -> None:
    """Test get_parser raises ValueError for unknown type."""
    with pytest.raises(ValueError, match="Unknown parser type"):
        get_parser("unknown_parser")
