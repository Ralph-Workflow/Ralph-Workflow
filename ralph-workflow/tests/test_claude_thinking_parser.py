"""Tests for ClaudeParser thinking_delta parsing."""

from __future__ import annotations

from ralph.agents.parsers.claude import ClaudeParser

_EXPECTED_TWO = 2


def _parse(lines: list[str]) -> list[object]:
    parser = ClaudeParser()
    return list(parser.parse(iter(lines)))


def test_thinking_delta_emits_thinking_type() -> None:
    lines = [
        '{"type":"message_start","message":{"id":"msg-1"}}',
        (
            '{"type":"content_block_start","index":0,'
            '"content_block":{"type":"thinking","thinking":""}}'
        ),
        (
            '{"type":"content_block_delta","index":0,'
            '"delta":{"type":"thinking_delta","thinking":"I consider this carefully"}}'
        ),
        '{"type":"content_block_stop","index":0}',
        '{"type":"message_stop"}',
    ]
    results = _parse(lines)
    thinking = [r for r in results if r.type == "thinking"]  # type: ignore[attr-defined]
    assert len(thinking) == 1
    assert thinking[0].content == "I consider this carefully"  # type: ignore[attr-defined]


def test_thinking_and_text_deltas_produce_distinct_types() -> None:
    lines = [
        '{"type":"message_start","message":{"id":"msg-1"}}',
        (
            '{"type":"content_block_start","index":0,'
            '"content_block":{"type":"thinking","thinking":""}}'
        ),
        (
            '{"type":"content_block_delta","index":0,'
            '"delta":{"type":"thinking_delta","thinking":"My reasoning"}}'
        ),
        '{"type":"content_block_stop","index":0}',
        (
            '{"type":"content_block_start","index":1,'
            '"content_block":{"type":"text","text":""}}'
        ),
        (
            '{"type":"content_block_delta","index":1,'
            '"delta":{"type":"text_delta","text":"My answer"}}'
        ),
        '{"type":"content_block_stop","index":1}',
        '{"type":"message_stop"}',
    ]
    results = _parse(lines)
    thinking = [r for r in results if r.type == "thinking"]  # type: ignore[attr-defined]
    text = [r for r in results if r.type == "text"]  # type: ignore[attr-defined]
    assert len(thinking) == 1
    assert len(text) == 1
    assert thinking[0].content == "My reasoning"  # type: ignore[attr-defined]
    assert text[0].content == "My answer"  # type: ignore[attr-defined]


def test_thinking_paragraph_boundary_flushes() -> None:
    lines = [
        '{"type":"message_start","message":{"id":"msg-1"}}',
        (
            '{"type":"content_block_start","index":0,'
            '"content_block":{"type":"thinking","thinking":""}}'
        ),
        (
            '{"type":"content_block_delta","index":0,'
            '"delta":{"type":"thinking_delta","thinking":"Para 1"}}'
        ),
        (
            '{"type":"content_block_delta","index":0,'
            '"delta":{"type":"thinking_delta","thinking":"\\n\\n"}}'
        ),
        (
            '{"type":"content_block_delta","index":0,'
            '"delta":{"type":"thinking_delta","thinking":"Para 2"}}'
        ),
        '{"type":"content_block_stop","index":0}',
        '{"type":"message_stop"}',
    ]
    results = _parse(lines)
    thinking = [r for r in results if r.type == "thinking"]  # type: ignore[attr-defined]
    assert len(thinking) == _EXPECTED_TWO
    assert thinking[0].content == "Para 1"  # type: ignore[attr-defined]
    assert thinking[1].content == "Para 2"  # type: ignore[attr-defined]


def test_existing_text_parsing_unaffected() -> None:
    lines = [
        '{"type":"message_start","message":{"id":"msg-1"}}',
        (
            '{"type":"content_block_start","index":0,'
            '"content_block":{"type":"text","text":""}}'
        ),
        (
            '{"type":"content_block_delta","index":0,'
            '"delta":{"type":"text_delta","text":"Hello"}}'
        ),
        '{"type":"content_block_stop","index":0}',
        '{"type":"message_stop"}',
    ]
    results = _parse(lines)
    text = [r for r in results if r.type == "text"]  # type: ignore[attr-defined]
    assert len(text) == 1
    assert text[0].content == "Hello"  # type: ignore[attr-defined]


def test_fallback_thinking_without_message_context() -> None:
    lines = [
        (
            '{"type":"content_block_delta","delta":'
            '{"type":"thinking_delta","thinking":"orphan thought"}}'
        ),
    ]
    results = _parse(lines)
    thinking = [r for r in results if r.type == "thinking"]  # type: ignore[attr-defined]
    assert len(thinking) == 1
    assert thinking[0].content == "orphan thought"  # type: ignore[attr-defined]
