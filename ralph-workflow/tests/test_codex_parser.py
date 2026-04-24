"""Tests for CodexParser lifecycle suppression and content routing."""

from __future__ import annotations

import json

from ralph.agents.parsers.codex import CodexParser


def test_thread_started_is_suppressed() -> None:
    """thread.started lifecycle event must produce no output."""
    parser = CodexParser()
    lines = [json.dumps({"type": "thread.started"})]
    results = list(parser.parse(iter(lines)))
    assert results == [], f"Expected empty list for thread.started, got: {results}"


def test_turn_started_is_suppressed() -> None:
    """turn.started lifecycle event must produce no output."""
    parser = CodexParser()
    lines = [json.dumps({"type": "turn.started"})]
    results = list(parser.parse(iter(lines)))
    assert results == [], f"Expected empty list for turn.started, got: {results}"


def test_message_start_is_suppressed() -> None:
    """message_start lifecycle event must produce no output."""
    parser = CodexParser()
    lines = [json.dumps({"type": "message_start"})]
    results = list(parser.parse(iter(lines)))
    assert results == [], f"Expected empty list for message_start, got: {results}"


def test_multiple_lifecycle_events_all_suppressed() -> None:
    """Multiple lifecycle events in sequence must all be suppressed."""
    parser = CodexParser()
    lines = [
        json.dumps({"type": "thread.started"}),
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "message_start"}),
    ]
    results = list(parser.parse(iter(lines)))
    assert results == [], f"Expected empty list for lifecycle sequence, got: {results}"


def test_text_event_is_not_suppressed() -> None:
    """Regular text event must still be emitted."""
    parser = CodexParser()
    lines = [json.dumps({"type": "text", "content": "hello world"})]
    results = list(parser.parse(iter(lines)))
    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "hello world"


def test_lifecycle_mixed_with_content_only_content_emitted() -> None:
    """Lifecycle events before/after real content must not appear in output."""
    parser = CodexParser()
    lines = [
        json.dumps({"type": "thread.started"}),
        json.dumps({"type": "text", "content": "real output"}),
        json.dumps({"type": "turn.started"}),
    ]
    results = list(parser.parse(iter(lines)))
    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "real output"
    non_text = [r for r in results if r.type not in ("text",)]
    assert non_text == [], f"Unexpected non-text results: {non_text}"


def test_stop_event_flushes_accumulator() -> None:
    """Stop event must flush any pending text accumulator."""
    parser = CodexParser()
    lines = [
        json.dumps({"type": "text_delta", "response_id": "r1", "delta": {"text": "hello"}}),
        json.dumps({"type": "turn.completed"}),
    ]
    results = list(parser.parse(iter(lines)))
    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "hello"


def test_item_started_reasoning_emits_thinking() -> None:
    """item.started with item.type=='reasoning' must emit type='thinking'."""
    parser = CodexParser()
    event = json.dumps(
        {
            "type": "item.started",
            "item": {"type": "reasoning", "text": "Thinking about X"},
        }
    )
    results = list(parser.parse(iter([event])))
    thinking = [r for r in results if r.type == "thinking"]
    assert len(thinking) == 1, f"Expected 1 thinking result, got: {results}"
    assert thinking[0].content == "Thinking about X"


def test_item_completed_reasoning_emits_thinking() -> None:
    """item.completed with item.type=='reasoning' must emit type='thinking'."""
    parser = CodexParser()
    event = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "reasoning", "text": "Done reasoning about X"},
        }
    )
    results = list(parser.parse(iter([event])))
    thinking = [r for r in results if r.type == "thinking"]
    assert len(thinking) == 1, f"Expected 1 thinking result, got: {results}"
    assert thinking[0].content == "Done reasoning about X"


def test_item_started_agent_message_emits_text() -> None:
    """item.started with item.type=='agent_message' must emit type='text'."""
    parser = CodexParser()
    event = json.dumps(
        {
            "type": "item.started",
            "item": {"type": "agent_message", "text": "Hello from agent"},
        }
    )
    results = list(parser.parse(iter([event])))
    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Hello from agent"


def test_reasoning_preserves_raw_and_metadata() -> None:
    """Reasoning thinking output must preserve raw and metadata from item object."""
    parser = CodexParser()
    event = json.dumps(
        {
            "type": "item.started",
            "item": {"type": "reasoning", "text": "Some thought", "id": "r-1"},
        }
    )
    results = list(parser.parse(iter([event])))
    thinking = [r for r in results if r.type == "thinking"]
    assert len(thinking) == 1
    assert thinking[0].raw is not None
    assert thinking[0].metadata is not None


def test_reasoning_empty_text_produces_no_output() -> None:
    """Reasoning item with empty text must not emit anything."""
    parser = CodexParser()
    event = json.dumps(
        {
            "type": "item.started",
            "item": {"type": "reasoning", "text": ""},
        }
    )
    results = list(parser.parse(iter([event])))
    thinking = [r for r in results if r.type == "thinking"]
    assert thinking == [], f"Expected no thinking output for empty text, got: {results}"
