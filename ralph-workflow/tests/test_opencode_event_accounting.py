"""Per-line accounting for the OpenCode parser.

The original defect was that the parser dropped frames silently,
so the per-line count tells the operator nothing was happening.
These tests pin the contract: every captured frame produces at
least one observable event, and the event count is within an
honest bound of the raw line count. The synthetic-fixture
regressions live in ``tests/test_opencode_display_fidelity.py``
and the captured-wire regressions live in
``tests/test_opencode_captured_wire.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.parsers.opencode import OpenCodeParser

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _parse(parser: OpenCodeParser, lines: Iterator[str]) -> list:
    return list(parser.parse(lines))


class TestOpenCodeEventAccounting:
    """Per-line accounting: ``parsed_event_count`` matches raw input."""

    def test_text_event_yields_a_text_line(self) -> None:
        parser = OpenCodeParser()
        line = '{"type": "text", "part": {"type": "text", "text": "hello"}}'
        results = _parse(parser, _lines(line))
        assert len(results) == 1
        assert results[0].type == "text"
        assert results[0].content == "hello"

    def test_tool_result_with_separate_result_field_yields_a_result_line(self) -> None:
        """OpenCode tool_result frames may carry a top-level ``result``
        field distinct from ``part.state.output``. The parser must
        route both shapes through ``_parse_tool_result``.
        """
        parser = OpenCodeParser()
        line = (
            '{"type": "tool_result", "tool": "Read", "result": "file contents",'
            ' "part": {"type": "tool", "tool": "Read",'
            ' "input": {"path": "x.py"}}}'
        )
        results = _parse(parser, _lines(line))
        assert len(results) == 1
        assert results[0].type == "tool_result"
        assert results[0].content == "file contents"

    def test_text_with_part_text_takes_precedence_over_top_level_content(self) -> None:
        parser = OpenCodeParser()
        line = (
            '{"type": "text", "content": "ignored", "part": {"type": "text",'
            ' "text": "used"}}'
        )
        results = _parse(parser, _lines(line))
        assert len(results) == 1
        assert results[0].type == "text"
        assert results[0].content == "used"
