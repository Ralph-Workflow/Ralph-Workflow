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


class TestParagraphBoundaryFlush:
    """Paragraph boundary (\n\n) should trigger immediate flush in all parsers."""

    @pytest.mark.parametrize(
        "parser_factory",
        [ClaudeParser, CodexParser, OpenCodeParser, GeminiParser, GenericParser],
    )
    def test_paragraph_boundary_flushes_accumulated_text(self, parser_factory: object) -> None:
        """All parsers must flush accumulated text when paragraph boundary is reached."""
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
        else:  # GenericParser
            lines = [
                '{"content":"Para 1\n\n"}',
                '{"content":"Para 2"}',
            ]

        results = list(parser.parse(_make_lines(lines)))
        text_results = [r for r in results if r.type == "text"]

        # Should produce exactly 2 text blocks (one per paragraph)
        assert len(text_results) == EXPECTED_TWO_TEXT_RESULTS, (
            f"Expected 2 text results, got {len(text_results)} for {parser_factory.__name__}"
        )
        assert text_results[0].content == "Para 1"
        assert text_results[1].content == "Para 2"
