"""Tests for GenericParser thought/reasoning → thinking type mapping."""

from __future__ import annotations

import json

import pytest

from ralph.agents.parsers.generic import GenericParser

pytestmark = pytest.mark.timeout_seconds(2)


def test_thought_field_emits_thinking() -> None:
    """A payload with top-level 'thought' field emits type='thinking'."""
    parser = GenericParser()
    line = json.dumps({"thought": "Thinking about Z"})
    results = list(parser.parse(iter([line])))
    thinking = [r for r in results if r.type == "thinking"]
    assert len(thinking) == 1, f"Expected 1 thinking result, got: {results}"
    assert thinking[0].content == "Thinking about Z"


def test_reasoning_field_emits_thinking() -> None:
    """A payload with top-level 'reasoning' field emits type='thinking'."""
    parser = GenericParser()
    line = json.dumps({"reasoning": "Reasoning about W"})
    results = list(parser.parse(iter([line])))
    thinking = [r for r in results if r.type == "thinking"]
    assert len(thinking) == 1, f"Expected 1 thinking result, got: {results}"
    assert thinking[0].content == "Reasoning about W"


def test_content_field_wins_over_thought() -> None:
    """When both 'content' and 'thought' exist, 'content' is used as type='text'."""
    parser = GenericParser()
    line = json.dumps({"content": "Real content", "thought": "Shadow thought"})
    results = list(parser.parse(iter([line])))
    thinking = [r for r in results if r.type == "thinking"]
    text_results = [r for r in results if r.type == "text"]
    assert thinking == [], f"'content' field must shadow 'thought', got thinking: {thinking}"
    assert len(text_results) == 1
    assert text_results[0].content == "Real content"


def test_text_field_wins_over_reasoning() -> None:
    """When both 'text' and 'reasoning' exist, 'text' is used as type='text'."""
    parser = GenericParser()
    line = json.dumps({"text": "Actual text", "reasoning": "Internal reasoning"})
    results = list(parser.parse(iter([line])))
    thinking = [r for r in results if r.type == "thinking"]
    text_results = [r for r in results if r.type == "text"]
    assert thinking == [], f"'text' field must shadow 'reasoning', got thinking: {thinking}"
    assert len(text_results) == 1
    assert text_results[0].content == "Actual text"


def test_message_field_wins_over_thought() -> None:
    """'message' field takes priority over 'thought'."""
    parser = GenericParser()
    line = json.dumps({"message": "User message", "thought": "Hidden thought"})
    results = list(parser.parse(iter([line])))
    thinking = [r for r in results if r.type == "thinking"]
    assert thinking == [], f"'message' field must shadow 'thought', got thinking: {thinking}"


def test_empty_thought_field_produces_no_output() -> None:
    """An empty 'thought' string must not emit anything."""
    parser = GenericParser()
    line = json.dumps({"thought": ""})
    results = list(parser.parse(iter([line])))
    thinking = [r for r in results if r.type == "thinking"]
    assert thinking == [], f"Empty thought must not emit thinking, got: {results}"


def test_empty_reasoning_field_produces_no_output() -> None:
    """An empty 'reasoning' string must not emit anything."""
    parser = GenericParser()
    line = json.dumps({"reasoning": ""})
    results = list(parser.parse(iter([line])))
    thinking = [r for r in results if r.type == "thinking"]
    assert thinking == [], f"Empty reasoning must not emit thinking, got: {results}"


def test_thought_type_is_thinking_not_text() -> None:
    """Confirming that thought fields yield type='thinking', not type='text'."""
    parser = GenericParser()
    line = json.dumps({"thought": "Deep contemplation"})
    results = list(parser.parse(iter([line])))
    text_results = [r for r in results if r.type == "text"]
    thinking = [r for r in results if r.type == "thinking"]
    assert text_results == [], f"thought must not emit text, got: {text_results}"
    assert len(thinking) == 1


def test_reasoning_type_is_thinking_not_text() -> None:
    """Confirming that reasoning fields yield type='thinking', not type='text'."""
    parser = GenericParser()
    line = json.dumps({"reasoning": "Step by step"})
    results = list(parser.parse(iter([line])))
    text_results = [r for r in results if r.type == "text"]
    thinking = [r for r in results if r.type == "thinking"]
    assert text_results == [], f"reasoning must not emit text, got: {text_results}"
    assert len(thinking) == 1


def test_thought_alongside_lifecycle_suppressed_type() -> None:
    """Lifecycle type suppresses the whole event; thought field is not reached."""
    parser = GenericParser()
    # type=thinking is in _LIFECYCLE_EVENT_TYPES, so it suppresses the whole event
    line = json.dumps({"type": "thinking", "thought": "hidden"})
    results = list(parser.parse(iter([line])))
    assert results == [], f"Lifecycle type must suppress entire event, got: {results}"


def test_plain_tool_line_emits_tool_use() -> None:
    """A clean '[plain] tool: NAME' line emits a tool_use with the tool name."""
    parser = GenericParser()
    results = list(parser.parse(iter(["[plain] tool: mcp__ralph__list_directory"])))
    tool_uses = [r for r in results if r.type == "tool_use"]
    assert len(tool_uses) == 1, f"Expected 1 tool_use, got: {results}"
    assert tool_uses[0].content == "mcp__ralph__list_directory"


def test_ansi_decorated_tool_line_still_emits_tool_use() -> None:
    """A '[plain] tool: NAME' line wrapped in ANSI color codes (nanocoder TUI
    output piped without a PTY) must still be detected as a tool_use, so MCP tool
    calls render live instead of being misclassified as raw content."""
    parser = GenericParser()
    ansi_line = "\x1b[36m[plain] tool: mcp__ralph__list_directory\x1b[0m"
    results = list(parser.parse(iter([ansi_line])))
    tool_uses = [r for r in results if r.type == "tool_use"]
    assert len(tool_uses) == 1, f"Expected 1 tool_use, got: {results}"
    assert tool_uses[0].content == "mcp__ralph__list_directory"


def test_agy_tool_use_patterns_detect_plain_text_tool_calls() -> None:
    """Plain-text tool-call prefixes yield tool_use AgentOutputLine instances."""
    parser = GenericParser()
    transcript = [
        "Calling tool: mcp__ralph__write_file",
        "Tool call: mcp__ralph__list_directory",
        "Read(tmp/agy-probe/hello.py)",
    ]
    results = list(parser.parse(iter(transcript)))
    tool_uses = [r for r in results if r.type == "tool_use"]
    assert len(tool_uses) == 3, f"Expected 3 tool_use lines, got: {results}"
    assert tool_uses[0].content == "mcp__ralph__write_file"
    assert tool_uses[1].content == "mcp__ralph__list_directory"


def test_agy_tool_result_pattern_detects_plain_text_tool_results() -> None:
    """Plain-text tool-result prefixes yield tool_result AgentOutputLine instances."""
    parser = GenericParser()
    transcript = [
        "Tool result: wrote file",
        "Result of: search complete",
    ]
    results = list(parser.parse(iter(transcript)))
    tool_results = [r for r in results if r.type == "tool_result"]
    assert len(tool_results) == 2, f"Expected 2 tool_result lines, got: {results}"
    assert tool_results[0].content == "wrote file"
    assert tool_results[1].content == "search complete"
