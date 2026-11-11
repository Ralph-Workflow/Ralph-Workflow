from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.parsers import (
    ClaudeParser,
    CodexParser,
    GeminiParser,
    GenericParser,
    OpenCodeParser,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

EXPECTED_SINGLE_TEXT_RESULT = 1
EXPECTED_TWO_TEXT_RESULTS = 2


def _make_lines(data: list[str]) -> Iterator[str]:
    return iter(data)


class TestParagraphBoundaryFlush:
    @pytest.mark.parametrize(
        "parser_factory",
        [ClaudeParser, CodexParser, OpenCodeParser, GeminiParser, GenericParser],
    )
    def test_paragraph_boundary_flushes_accumulated_text(self, parser_factory: type) -> None:
        parser = parser_factory()

        if parser_factory in (ClaudeParser,):
            lines = [
                '{"type":"content_block_delta","delta":{"type":"text_delta","text":"Para 1\n\n"}}',
                '{"type":"content_block_delta","delta":{"type":"text_delta","text":"Para 2"}}',
            ]
        elif parser_factory is CodexParser:
            lines = [
                '{"type":"text_delta","delta":"Para 1\n\n","response_id":"resp-1"}',
                '{"type":"text_delta","delta":"Para 2","response_id":"resp-1"}',
            ]
        elif parser_factory is OpenCodeParser:
            lines = [
                '{"type":"step_start","id":"step-1"}',
                '{"type":"stream","content":"Para 1\n\n"}',
                '{"type":"stream","content":"Para 2"}',
                '{"type":"step_finish","id":"step-1"}',
            ]
        elif parser_factory is GeminiParser:
            lines = [
                'data: {"type":"text","content":"Para 1\n\n"}',
                'data: {"type":"text","content":"Para 2"}',
            ]
        else:
            lines = [
                '{"content":"Para 1\n\n"}',
                '{"content":"Para 2"}',
            ]

        results = list(parser.parse(_make_lines(lines)))
        text_results = [r for r in results if r.type == "text"]

        assert len(text_results) == EXPECTED_TWO_TEXT_RESULTS, (
            f"Expected 2 text results, got {len(text_results)} for {parser_factory.__name__}"
        )
        assert text_results[0].content == "Para 1"
        assert text_results[1].content == "Para 2"


class TestIteratorExhaustionFlush:
    @pytest.mark.parametrize(
        "parser_factory",
        [ClaudeParser, CodexParser, OpenCodeParser, GeminiParser, GenericParser],
    )
    def test_iterator_exhaustion_flushes_pending_accumulator(self, parser_factory: type) -> None:
        parser = parser_factory()

        if parser_factory is ClaudeParser:
            lines = [
                '{"type":"content_block_delta","delta":{"type":"text_delta","text":"Partial"}}',
                '{"type":"content_block_delta","delta":{"type":"text_delta","text":" text"}}',
            ]
        elif parser_factory is CodexParser:
            lines = [
                '{"type":"text_delta","delta":"Partial","response_id":"resp-1"}',
                '{"type":"text_delta","delta":" text","response_id":"resp-1"}',
            ]
        elif parser_factory is OpenCodeParser:
            lines = [
                '{"type":"step_start","id":"step-1"}',
                '{"type":"stream","content":"Partial"}',
                '{"type":"stream","content":" text"}',
            ]
        elif parser_factory is GeminiParser:
            lines = [
                'data: {"type":"text","content":"Partial"}',
                'data: {"type":"text","content":" text"}',
            ]
        else:
            lines = [
                '{"content":"Partial"}',
                '{"content":" text"}',
            ]

        results = list(parser.parse(_make_lines(lines)))
        text_results = [r for r in results if r.type == "text"]

        assert len(text_results) == EXPECTED_SINGLE_TEXT_RESULT, (
            "Expected 1 text result on exhaustion, got "
            f"{len(text_results)} for {parser_factory.__name__}"
        )
        assert text_results[0].content == "Partial text"


class TestEmptyFlushSuppression:
    @pytest.mark.parametrize(
        "parser_factory",
        [ClaudeParser, CodexParser, OpenCodeParser, GeminiParser, GenericParser],
    )
    def test_empty_content_at_boundary_not_emitted(self, parser_factory: type) -> None:
        parser = parser_factory()

        if parser_factory is ClaudeParser:
            lines = [
                '{"type":"message_start","message":{"id":"123"}}',
                '{"type":"content_block_start","content_block":{"type":"text"}}',
                '{"type":"content_block_delta","delta":{"type":"text_delta","text":""}}',
                (
                    '{"type":"content_block_delta","delta":'
                    '{"type":"text_delta","text":"Actual content"}}'
                ),
                '{"type":"content_block_stop"}',
                '{"type":"message_stop"}',
            ]
        elif parser_factory is CodexParser:
            lines = [
                '{"type":"text_delta","delta":"","response_id":"resp-1"}',
                '{"type":"text_delta","delta":"Actual content","response_id":"resp-1"}',
                '{"type":"response.completed","response_id":"resp-1"}',
            ]
        elif parser_factory is OpenCodeParser:
            lines = [
                '{"type":"step_start","id":"step-1"}',
                '{"type":"stream","content":""}',
                '{"type":"stream","content":"Actual content"}',
                '{"type":"step_finish","id":"step-1"}',
            ]
        elif parser_factory is GeminiParser:
            lines = [
                'data: {"type":"text","content":""}',
                'data: {"type":"text","content":"Actual content"}',
                'data: {"type":"done"}',
            ]
        else:
            lines = [
                '{"content":""}',
                '{"content":"Actual content"}',
            ]

        results = list(parser.parse(_make_lines(lines)))
        text_results = [r for r in results if r.type == "text"]
        content_values = [r.content for r in text_results]

        assert "" not in content_values, (
            f"Empty content should not appear for {parser_factory.__name__}"
        )
        assert "Actual content" in content_values


class TestNoMidTokenSplit:
    @pytest.mark.parametrize(
        "parser_factory",
        [ClaudeParser, CodexParser, OpenCodeParser, GeminiParser, GenericParser],
    )
    def test_delta_accumulation_produces_coherent_output(self, parser_factory: type) -> None:
        parser = parser_factory()

        if parser_factory is ClaudeParser:
            lines = [
                '{"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}',
                '{"type":"content_block_delta","delta":{"type":"text_delta","text":" World"}}',
                '{"type":"content_block_delta","delta":{"type":"text_delta","text":"!"}}',
                '{"type":"content_block_stop"}',
                '{"type":"message_stop"}',
            ]
        elif parser_factory is CodexParser:
            lines = [
                '{"type":"text_delta","delta":"Hello","response_id":"resp-1"}',
                '{"type":"text_delta","delta":" World","response_id":"resp-1"}',
                '{"type":"text_delta","delta":"!","response_id":"resp-1"}',
                '{"type":"response.completed","response_id":"resp-1"}',
            ]
        elif parser_factory is OpenCodeParser:
            lines = [
                '{"type":"step_start","id":"step-1"}',
                '{"type":"stream","content":"Hello"}',
                '{"type":"stream","content":" World"}',
                '{"type":"stream","content":"!"}',
                '{"type":"step_finish","id":"step-1"}',
            ]
        elif parser_factory is GeminiParser:
            lines = [
                'data: {"type":"text","content":"Hello"}',
                'data: {"type":"text","content":" World"}',
                'data: {"type":"text","content":"!"}',
                'data: {"type":"done"}',
            ]
        else:
            lines = [
                '{"content":"Hello"}',
                '{"content":" World"}',
                '{"content":"!"}',
            ]

        results = list(parser.parse(_make_lines(lines)))
        text_results = [r for r in results if r.type == "text"]

        assert len(text_results) == EXPECTED_SINGLE_TEXT_RESULT, (
            f"Expected 1 coherent block, got {len(text_results)} for {parser_factory.__name__}"
        )
        assert text_results[0].content == "Hello World!"
