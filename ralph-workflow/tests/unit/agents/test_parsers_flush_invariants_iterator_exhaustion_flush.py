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


class TestIteratorExhaustionFlush:
    """Iterator exhaustion must trigger final flush of accumulated content."""

    @pytest.mark.parametrize(
        "parser_factory",
        [ClaudeParser, CodexParser, OpenCodeParser, GeminiParser, GenericParser],
    )
    def test_iterator_exhaustion_flushes_pending_accumulator(self, parser_factory: object) -> None:
        """All parsers must flush remaining accumulator when iterator ends."""
        parser = parser_factory()

        if parser_factory is ClaudeParser:
            lines = [
                '{"type":"content_block_delta","delta":{"type":"text_delta","text":"Partial"}}',
                '{"type":"content_block_delta","delta":{"type":"text_delta","text":" text"}}',
                # No message_stop - just iterator exhaustion
            ]
        elif parser_factory is CodexParser:
            lines = [
                '{"type":"text_delta","delta":"Partial","response_id":"resp-1"}',
                '{"type":"text_delta","delta":" text","response_id":"resp-1"}',
                # No response.completed
            ]
        elif parser_factory is OpenCodeParser:
            lines = [
                '{"type":"step_start","id":"step-1"}',
                '{"type":"stream","content":"Partial"}',
                '{"type":"stream","content":" text"}',
                # No step_finish or done
            ]
        elif parser_factory is GeminiParser:
            lines = [
                'data: {"type":"text","content":"Partial"}',
                'data: {"type":"text","content":" text"}',
                # No done
            ]
        else:  # GenericParser
            lines = [
                '{"content":"Partial"}',
                '{"content":" text"}',
                # Iterator exhaustion
            ]

        results = list(parser.parse(_make_lines(lines)))
        text_results = [r for r in results if r.type == "text"]

        # Should flush accumulated content on iterator exhaustion
        assert len(text_results) == EXPECTED_SINGLE_TEXT_RESULT, (
            "Expected 1 text result on exhaustion, got "
            f"{len(text_results)} for {parser_factory.__name__}"
        )
        assert text_results[0].content == "Partial text"
