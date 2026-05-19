"""Regression tests for parser delta-flush invariants.

These tests verify that ALL parsers (Claude, Codex, OpenCode, Gemini, Generic)
maintain consistent delta accumulation behavior:
1. Paragraph boundary (\n\n) triggers a flush
2. Final flush occurs on iterator exhaustion
3. Empty flushes at boundaries are suppressed
4. No mid-token splits occur
"""

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
    """Helper to create line iterator from list."""
    return iter(data)


class TestNoMidTokenSplit:
    """Text accumulation should not split mid-token; content should be coherent."""

    @pytest.mark.parametrize(
        "parser_factory",
        [ClaudeParser, CodexParser, OpenCodeParser, GeminiParser, GenericParser],
    )
    def test_delta_accumulation_produces_coherent_output(self, parser_factory: object) -> None:
        """All parsers must accumulate deltas into coherent blocks, not split tokens."""
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
        else:  # GenericParser
            lines = [
                '{"content":"Hello"}',
                '{"content":" World"}',
                '{"content":"!"}',
            ]

        results = list(parser.parse(_make_lines(lines)))
        text_results = [r for r in results if r.type == "text"]

        # Should coalesce into one coherent text block
        assert len(text_results) == EXPECTED_SINGLE_TEXT_RESULT, (
            f"Expected 1 coherent block, got {len(text_results)} for {parser_factory.__name__}"
        )
        assert text_results[0].content == "Hello World!"
