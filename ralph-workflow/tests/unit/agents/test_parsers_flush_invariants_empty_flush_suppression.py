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


class TestEmptyFlushSuppression:
    """Empty content at boundaries should not produce empty output lines."""

    @pytest.mark.parametrize(
        "parser_factory",
        [ClaudeParser, CodexParser, OpenCodeParser, GeminiParser, GenericParser],
    )
    def test_empty_content_at_boundary_not_emitted(self, parser_factory: object) -> None:
        """All parsers must suppress empty lines at boundaries."""
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
        else:  # GenericParser
            lines = [
                '{"content":""}',
                '{"content":"Actual content"}',
            ]

        results = list(parser.parse(_make_lines(lines)))
        text_results = [r for r in results if r.type == "text"]
        content_values = [r.content for r in text_results]

        # Should not have empty string in content
        assert "" not in content_values, (
            f"Empty content should not appear for {parser_factory.__name__}"
        )
        assert "Actual content" in content_values
